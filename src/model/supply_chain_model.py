"""
Worldwide critical-minerals supply-chain model.

Per-facility mines, processors, recyclers; per-country manufacturers,
retailers, consumers; per-country transport fleets routed through real
maritime/overland routes with chokepoint-aware delivery delays.

Tier-ordered scheduler: mines -> recyclers -> processors -> manufacturers
-> retailers -> consumers -> transport (last so shipments accepted
earlier in the same step queue with the full lead time).
"""

from mesa import Model
from mesa.datacollection import DataCollector
import numpy as np
import random

from ..agents.mine_agent import MineAgent
from ..agents.processor_agent import ProcessorAgent
from ..agents.transport_agent import TransportAgent
from ..agents.manufacturer_agent import ManufacturerAgent
from ..agents.retailer_agent import RetailerAgent
from ..agents.consumer_agent import ConsumerAgent
from ..agents.recycling_agent import RecyclingAgent

from ..data.data_loader import load_mineral_data
from ..data.routing import CHOKEPOINTS, select_route_or_fallback
from .market_mechanism import (
    update_price, marginal_cost, cheapest_active_cost,
    check_geopolitical_event,
    select_affected_jurisdiction, calculate_disruption_duration,
)


class MineralSupplyChainModel(Model):
    """Agent-based model of a worldwide critical-minerals supply chain."""

    def __init__(self, config):
        """Initialize the supply chain model from a per-mineral config."""
        super().__init__()

        self.config = config
        self.mineral_type = config["mineral_type"]

        seed = config.get("random_seed", 42)
        random.seed(seed)
        np.random.seed(seed)
        self.random_state = random.Random(seed)

        # Price state
        self.initial_price = config["initial_price"]
        self.current_price = self.initial_price
        self.price_floor = config["price_floor"]
        self.price_ceiling = config["price_ceiling"]

        # End-of-life pool for recycling (dict: step -> mineral tons)
        self.end_of_life_pool = {}
        self.product_lifetime = config.get("product_lifetime_steps", 25)
        self._eol_initial_this_step = 0.0

        # Geopolitical event tracking
        self.active_disruptions = {}  # jurisdiction -> remaining_steps

        # Political-embargo tracking
        self.scheduled_embargoes = list(config.get("political_embargoes", []))
        self.active_embargoes = {}    # jurisdiction -> remaining_steps

        # Chokepoint crisis tracking
        self.scheduled_chokepoint_crises = list(
            config.get("chokepoint_crises", [])
        )
        # closed_chokepoints maps name -> end_step (exclusive). Looked up
        # by transport agents when deciding whether to defer delivery,
        # and by the routing engine when picking a route at dispatch time.
        self.closed_chokepoints = {}

        # Rolling supply/demand history for the price signal
        self.supply_flow_history: list = []
        self.demand_flow_history: list = []

        # Cost-curve tracking (set by _update_price each step; exposed
        # on the data collector as Marginal_Cost / Cheapest_Active_Cost
        # so users can see the dynamic price band).
        self._marginal_cost = 0.0
        self._cheapest_cost = 0.0

        # Agent lists per tier
        self.mines = []
        self.recyclers = []
        self.processors = []
        self.manufacturers = []
        self.retailers = []
        self.consumers = []
        self.transport_agents = []

        # Time counter. Mesa's BaseScheduler is not used here -- the
        # supply-chain ordering is enforced by the per-tier loop in
        # ``step()`` rather than a Mesa activation strategy, so all
        # time-tracking flows through ``current_step``.
        self.current_step = 0

        self._load_data_and_create_agents()
        self._setup_data_collector()

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    @property
    def tiers(self):
        return [
            self.mines,
            self.recyclers,
            self.processors,
            self.manufacturers,
            self.retailers,
            self.consumers,
            self.transport_agents,
        ]

    def _load_data_and_create_agents(self):
        """Load all CSVs and build the agent population."""
        data = load_mineral_data(self.mineral_type)
        self._resolve_baseline_demand(data['demand'])

        # Creation order matches the tier order; transport last because
        # downstream agents reference no transport-specific state at init.
        self._create_mines(data['mines'])
        self._create_recyclers(data['recyclers'])
        self._create_processors(data['processors'])
        self._create_manufacturers(data['manufacturer_countries'])
        self._create_retailers(data['consumer_countries'])
        self._create_consumers(data['consumer_countries'])
        self._create_transport(data['transport_fleet'])

    def _resolve_baseline_demand(self, demand_data):
        """Set baseline_mineral_demand_per_step + build the demand curve.

        Reads any (year, scenario) rows present in demand.csv and builds a
        list of (step, annual_demand) knots. baseline_* attributes are
        anchored at the 2024 row so existing capacity-sizing logic still
        works; demand growth is applied via a multiplier returned by
        ``demand_growth_factor(step)``. The 2024 row is the anchor; later
        rows (e.g., 2030_NetZero, 2050_NetZero) drive linear interpolation
        between knots so a 24-year run actually sees demand grow.
        """
        baseline = 0.0
        if demand_data and '2024' in demand_data and demand_data['2024'] > 0:
            baseline = float(demand_data['2024'])
            print(f"Using 2024 demand: {baseline:,.0f} tons/year")
        if baseline <= 0:
            baseline = float(self.config.get('default_annual_demand_tons', 100000.0))
            print(f"No 2024 demand row; using config default: {baseline:,.0f} tons/year")

        steps_per_year = self.config.get('steps_per_year', 52)
        intensity = self.config.get('manufacturer_mineral_intensity', 0.008)
        base_year = int(self.config.get('base_year', 2024))

        self.baseline_mineral_demand_tons_per_year = baseline
        self.baseline_mineral_demand_per_step = baseline / steps_per_year
        self.baseline_product_demand_per_step = self.baseline_mineral_demand_per_step / intensity

        # Build demand-curve knots: [(step, annual_demand_tons), ...]
        # The 2024 (base-year) row is always step 0. Future rows (e.g.,
        # '2030_NetZero', '2050_NetZero') are placed at the corresponding
        # step. We pick the row whose scenario matches the configured
        # 'demand_scenario' (default 'NetZero') for each forward year.
        scenario = self.config.get('demand_scenario', 'NetZero')
        knots = [(0, baseline)]
        if demand_data:
            for key, val in demand_data.items():
                if key == str(base_year):
                    continue
                # Keys take the form 'YYYY_scenario' for non-baseline rows.
                if '_' not in key:
                    continue
                year_str, _, scen = key.partition('_')
                try:
                    year = int(year_str)
                except ValueError:
                    continue
                if scen != scenario:
                    continue
                step = (year - base_year) * steps_per_year
                if step > 0:
                    knots.append((step, float(val)))
        knots.sort()
        self._demand_knots = knots

        if len(knots) > 1:
            knot_summary = ", ".join(
                f"step {s} -> {d:,.0f} t/yr" for s, d in knots
            )
            print(f"Demand trajectory ({scenario}): {knot_summary}")

    def annual_demand_at(self, step):
        """Linearly interpolate annual mineral demand at a given step.

        Falls back to the first (last) knot for steps before (after) the
        knot range. Always returns a finite, positive value as long as
        the baseline knot is positive.
        """
        knots = getattr(self, '_demand_knots', None)
        if not knots:
            return self.baseline_mineral_demand_tons_per_year
        if step <= knots[0][0]:
            return knots[0][1]
        for i in range(1, len(knots)):
            s0, d0 = knots[i - 1]
            s1, d1 = knots[i]
            if step <= s1:
                if s1 == s0:
                    return d1
                frac = (step - s0) / (s1 - s0)
                return d0 + frac * (d1 - d0)
        return knots[-1][1]

    def demand_growth_factor(self, step=None):
        """Return demand multiplier vs. the 2024 baseline at a given step."""
        if step is None:
            step = self.current_step
        base = self.baseline_mineral_demand_tons_per_year
        if base <= 0:
            return 1.0
        return self.annual_demand_at(step) / base

    def _create_mines(self, mines_data):
        steps_per_year = self.config.get('steps_per_year', 52)
        for m in mines_data:
            mine = MineAgent(
                unique_id=self.next_id(),
                model=self,
                jurisdiction=m['country'],
                facility=m['facility'],
                production_capacity=m['production_capacity'] / steps_per_year,
                extraction_cost=m['extraction_cost'],
                reserves=m['reserves'],
            )
            self.mines.append(mine)
        print(f"Created {len(self.mines)} mines across "
              f"{len({a.country for a in self.mines})} countries")

    def _create_recyclers(self, recyclers_data):
        """Create per-facility recyclers.

        Each recycler claims a fraction of the EOL bucket sized to its
        share of total recycling capacity. Recovery efficiency comes from
        the per-facility row. Per-step intake is capped at the
        facility's nameplate capacity (capacity_yr / steps_per_year)
        so a small recycler can't absorb an arbitrary share of a huge
        EOL bucket.
        """
        total_capacity = sum(r['capacity'] for r in recyclers_data) or 1.0
        # Aggregate collection rate across all facilities (config knob;
        # default 30% of available EOL across all recyclers per step).
        agg_collection = self.config.get("collection_rate", 0.30)
        steps_per_year = self.config.get('steps_per_year', 52)

        for r in recyclers_data:
            share = r['capacity'] / total_capacity
            recycler = RecyclingAgent(
                unique_id=self.next_id(),
                model=self,
                country=r['country'],
                facility=r['facility'],
                collection_rate=agg_collection * share,
                recovery_efficiency=r['recovery_efficiency'],
                processing_cost=r['processing_cost'],
                capacity_per_step=r['capacity'] / steps_per_year,
            )
            self.recyclers.append(recycler)
        print(f"Created {len(self.recyclers)} recyclers across "
              f"{len({a.country for a in self.recyclers})} countries")

    def _create_processors(self, processors_data):
        """Create per-facility processors. Capacity from data, in t/year."""
        steps_per_year = self.config.get('steps_per_year', 52)
        warmstart_mult = self.config.get("processor_warmstart_safety_multiplier", 2.0)

        for p in processors_data:
            processor = ProcessorAgent(
                unique_id=self.next_id(),
                model=self,
                country=p['country'],
                facility=p['facility'],
                conversion_efficiency=p['conversion_efficiency'],
                energy_cost=p['energy_cost'],
                capacity=p['capacity'] / steps_per_year,
            )
            processor.inventory = processor.safety_stock * warmstart_mult
            self.processors.append(processor)
        print(f"Created {len(self.processors)} processors across "
              f"{len({a.country for a in self.processors})} countries")

    def _create_manufacturers(self, country_shares):
        """One manufacturer per producing country, sized to share of global product demand."""
        intensity = self.config.get("manufacturer_mineral_intensity", 0.008)
        headroom = self.config.get("manufacturer_capacity_headroom", 1.5)
        warmstart_frac = self.config.get("manufacturer_warmstart_input_fraction", 0.5)

        # Total nameplate sized to baseline product demand x headroom.
        total_capacity = self.baseline_product_demand_per_step * headroom

        for entry in country_shares:
            country = entry['country']
            share = entry['share']
            cap = total_capacity * share
            mfr = ManufacturerAgent(
                unique_id=self.next_id(),
                model=self,
                country=country,
                mineral_intensity=intensity,
                production_capacity=cap,
            )
            mfr.input_inventory = mfr.target_inventory * warmstart_frac
            self.manufacturers.append(mfr)
        print(f"Created {len(self.manufacturers)} manufacturer aggregates "
              f"(one per producing country)")

    def _create_retailers(self, country_shares):
        """One retailer per consumer country, sized to that country's demand share."""
        reorder_mult = self.config.get("retailer_reorder_point_multiplier", 2.0)
        order_mult = self.config.get("retailer_order_quantity_multiplier", 3.0)
        intensity = self.config.get("manufacturer_mineral_intensity", 0.008)

        for entry in country_shares:
            country = entry['country']
            share = entry['share']
            country_demand = self.baseline_product_demand_per_step * share
            retailer = RetailerAgent(
                unique_id=self.next_id(),
                model=self,
                country=country,
                reorder_point=country_demand * reorder_mult,
                order_quantity=country_demand * order_mult,
            )
            # Warm-start the embedded mineral content of starting inventory
            # to the baseline manufacturer intensity. Without this, the
            # first product-lifetime cycle of EOL deposits is silently
            # zeroed because the warm-start stock is treated as having no
            # mineral content -- biasing recycling rates downward for the
            # first ~10 years of any run.
            retailer.inventory_mineral = retailer.inventory * intensity
            self.retailers.append(retailer)
        print(f"Created {len(self.retailers)} retailers (one per consumer country)")

    def _create_consumers(self, country_shares):
        """One consumer per country, sized to that country's share of global demand."""
        price_sensitivity = self.config.get("consumer_price_sensitivity", -0.8)

        for entry in country_shares:
            country = entry['country']
            share = entry['share']
            base_demand = self.baseline_product_demand_per_step * share
            consumer = ConsumerAgent(
                unique_id=self.next_id(),
                model=self,
                country=country,
                base_demand=base_demand,
                price_sensitivity=price_sensitivity,
            )
            self.consumers.append(consumer)
        print(f"Created {len(self.consumers)} consumers (one per consumer country)")

    def _create_transport(self, fleet_data):
        """Create per-country transport agents from the fleet table."""
        costs = {
            'ship':  self.config.get("transport_cost_ship", 10),
            'rail':  self.config.get("transport_cost_rail", 25),
            'truck': self.config.get("transport_cost_truck", 50),
        }
        for entry in fleet_data:
            country = entry['country']
            mode = entry['mode']
            for _ in range(entry['n_agents']):
                t = TransportAgent(
                    unique_id=self.next_id(),
                    model=self,
                    country=country,
                    mode=mode,
                    cost_per_unit=costs.get(mode, 20),
                    capacity=entry['capacity_per_agent'],
                )
                self.transport_agents.append(t)
        print(f"Created {len(self.transport_agents)} transport agents across "
              f"{len({a.country for a in self.transport_agents})} countries")

    # ------------------------------------------------------------------
    # Routing / dispatch
    # ------------------------------------------------------------------

    def select_transport(self, mode, prefer_country=None):
        """Pick a transport agent of the given mode, preferring a country.

        Returns the chosen agent, or None if the fleet is empty.

        If no agent of ``mode`` exists *anywhere* in the fleet (a data
        problem -- e.g. routing returns 'rail' but no rail agent was
        loaded), we fall back to a mode-agnostic pick so the simulation
        keeps running, but emit a one-time warning per missing mode so
        the gap is visible. Previously this fallback was silent and a
        ship route could end up carried by a truck agent without notice.
        """
        if not self.transport_agents:
            return None
        mode_pool = [t for t in self.transport_agents if t.mode == mode]
        if not mode_pool:
            if not hasattr(self, '_warned_missing_modes'):
                self._warned_missing_modes = set()
            if mode not in self._warned_missing_modes:
                print(
                    f"Warning: no transport agents of mode '{mode}' in the "
                    f"fleet; subsequent shipments routed through this mode "
                    f"will fall back to a mode-agnostic carrier."
                )
                self._warned_missing_modes.add(mode)
            mode_pool = list(self.transport_agents)
        if prefer_country:
            local = [t for t in mode_pool if t.country == prefer_country]
            if local:
                return self.random_state.choice(local)
        return self.random_state.choice(mode_pool)

    def dispatch_shipment(self, material_type, quantity, origin_country,
                          dest_country, destination, mineral_tons=0.0):
        """Pick a route + transport agent and queue the shipment.

        Returns True if dispatched. Even when chokepoints are closed, the
        shipment is dispatched onto the longest-fallback route; the
        TransportAgent's per-step delivery check defers arrival until the
        chokepoint reopens.
        """
        if quantity <= 0:
            return False

        # routing.select_route_or_fallback always returns a route (the
        # routing module's get_routes falls back to a generic 5-week
        # ship leg for unknown country pairs), so we don't need an extra
        # None-guard here.
        chokepoints, lead_time, mode = select_route_or_fallback(
            origin_country, dest_country, set(self.closed_chokepoints),
        )

        transport = self.select_transport(mode, prefer_country=origin_country)
        if transport is None:
            # No fleet at all; deliver immediately to keep simulation alive.
            destination.receive_shipment(material_type, quantity,
                                         mineral_tons, origin_country)
            return True

        transport.accept_shipment(
            material_type=material_type,
            quantity=quantity,
            destination=destination,
            origin_jurisdiction=origin_country,
            dest_jurisdiction=dest_country,
            mineral_tons=mineral_tons,
            chokepoints=chokepoints,
            lead_time=lead_time,
        )
        return True

    # ------------------------------------------------------------------
    # Per-step orchestration
    # ------------------------------------------------------------------

    def step(self):
        # 1. Update disruption / embargo / chokepoint state first.
        self._check_geopolitical_events()
        self._check_political_embargoes()
        self._check_chokepoint_crises()

        # 2. Snapshot EOL bucket for fair recycler share-out.
        self._eol_initial_this_step = float(
            self.end_of_life_pool.get(self.current_step, 0.0)
        )

        # 3. Activate agents in supply-chain order; shuffle within tier.
        for tier in self.tiers:
            order = list(tier)
            self.random_state.shuffle(order)
            for agent in order:
                agent.step()

        # 4. Update global price.
        self._update_price()
        # 5. Collect data.
        self.datacollector.collect(self)
        # 6. Increment step counter.
        self.current_step += 1

    def _check_geopolitical_events(self):
        for jurisdiction in list(self.active_disruptions):
            remaining = self.active_disruptions[jurisdiction]
            if remaining <= 1:
                del self.active_disruptions[jurisdiction]
            else:
                self.active_disruptions[jurisdiction] = remaining - 1

        geo_prob = self.config.get("geopolitical_event_probability", 0.01)
        if check_geopolitical_event(geo_prob, self.random_state):
            self._trigger_geopolitical_event()

    def _check_political_embargoes(self):
        for country in list(self.active_embargoes):
            remaining = self.active_embargoes[country]
            if remaining <= 1:
                del self.active_embargoes[country]
                print(f"Political embargo lifted: {country} resumes export at "
                      f"step {self.current_step}")
            else:
                self.active_embargoes[country] = remaining - 1

        for emb in self.scheduled_embargoes:
            if emb.get('start_step') == self.current_step:
                country = emb['country']
                duration = int(emb['duration'])
                self.active_embargoes[country] = max(
                    self.active_embargoes.get(country, 0), duration,
                )
                print(f"Political embargo: {country} withholds export for "
                      f"{duration} steps (starting step {self.current_step})")

    def _check_chokepoint_crises(self):
        """Tick existing chokepoint closures and start scheduled ones.

        ``end_step`` is set when the crisis fires as ``start_step +
        duration`` and represents the *first open step*. The chokepoint
        should therefore be removed when ``current_step`` reaches
        ``end_step`` -- not one step earlier. The previous condition
        ``end <= current_step + 1`` shortened every closure by one step
        (an 8-week Suez crisis was actually 7 weeks of closure).
        """
        for cp in list(self.closed_chokepoints):
            end = self.closed_chokepoints[cp]
            if end <= self.current_step:
                del self.closed_chokepoints[cp]
                print(f"Chokepoint reopened: {cp} at step {self.current_step}")

        for crisis in self.scheduled_chokepoint_crises:
            if crisis.get('start_step') == self.current_step:
                cp = crisis['chokepoint']
                duration = int(crisis['duration'])
                if cp not in CHOKEPOINTS:
                    print(f"Warning: unknown chokepoint '{cp}' (known: {CHOKEPOINTS})")
                    continue
                end_step = self.current_step + duration
                existing = self.closed_chokepoints.get(cp, 0)
                self.closed_chokepoints[cp] = max(existing, end_step)
                print(f"Chokepoint crisis: {cp} closed for {duration} steps "
                      f"(starting step {self.current_step})")

    def is_embargoed(self, jurisdiction):
        return jurisdiction in self.active_embargoes

    def _trigger_geopolitical_event(self):
        jurisdictions = list({mine.jurisdiction for mine in self.mines})
        if not jurisdictions:
            return
        affected = select_affected_jurisdiction(jurisdictions, self.random_state)
        min_dur = self.config.get("geopolitical_duration_min", 5)
        max_dur = self.config.get("geopolitical_duration_max", 15)
        duration = calculate_disruption_duration(min_dur, max_dur, self.random_state)

        self.active_disruptions[affected] = duration
        for mine in self.mines:
            if mine.jurisdiction == affected:
                mine.apply_geopolitical_disruption(duration)
        for transport in self.transport_agents:
            transport.apply_disruption(affected, duration)

        print(f"Geopolitical event! {affected} disrupted for {duration} steps")

    def _update_price(self):
        supply_this_step = (
            sum(m.available_production_this_step for m in self.mines)
            + sum(r.recycled_this_step for r in self.recyclers)
        )

        total_product_demand = sum(c.current_demand for c in self.consumers)
        if self.manufacturers:
            avg_intensity = (
                sum(m.mineral_intensity for m in self.manufacturers)
                / len(self.manufacturers)
            )
        else:
            avg_intensity = self.config.get("manufacturer_mineral_intensity", 0.008)
        demand_this_step = max(total_product_demand * avg_intensity, 1e-9)

        window = int(self.config.get("price_signal_window_steps", 8))
        self.supply_flow_history.append(supply_this_step)
        self.demand_flow_history.append(demand_this_step)
        if len(self.supply_flow_history) > window:
            self.supply_flow_history.pop(0)
            self.demand_flow_history.pop(0)

        supply_smoothed = sum(self.supply_flow_history) / len(self.supply_flow_history)
        demand_smoothed = sum(self.demand_flow_history) / len(self.demand_flow_history)

        # Cost-anchored price update. marginal_cost is the merit-order
        # cost of the last operational mine called online to meet
        # demand; the price gravitates toward this in the absence of
        # imbalance. The cost curve is computed live from the mine
        # state so depletion / mothballing / capacity expansion all
        # flow through to the price band.
        #
        # ``offline_premium`` lifts the all-mines-mothballed fallback
        # above the cheapest extraction cost. Without it, the soft
        # floor would pin the price exactly at extraction_cost -- which
        # is below the restart threshold (extraction_cost * 1.2) --
        # creating a self-locking trap where everyone mothballs and no
        # one restarts. The premium matches the restart margin so the
        # implied break-even price is the price needed to actually
        # bring something back online.
        restart_margin = float(self.config.get("mine_restart_margin", 1.2))
        self._marginal_cost = marginal_cost(
            self.mines, demand_smoothed, offline_premium=restart_margin,
        )
        self._cheapest_cost = cheapest_active_cost(
            self.mines, offline_premium=restart_margin,
        )

        elasticity = float(self.config.get("price_elasticity", 0.4))
        max_step_pct = float(self.config.get("price_max_step_pct", 0.15))
        anchor_strength = float(self.config.get("price_anchor_strength", 0.05))
        ceiling_mc_multiple = float(self.config.get("price_ceiling_mc_multiple", 8.0))
        floor_cost_fraction = float(self.config.get("price_floor_cost_fraction", 0.6))

        self.current_price = update_price(
            self.current_price,
            supply_smoothed,
            demand_smoothed,
            self._marginal_cost,
            self._cheapest_cost,
            elasticity=elasticity,
            max_step_pct=max_step_pct,
            anchor_strength=anchor_strength,
            ceiling_mc_multiple=ceiling_mc_multiple,
            floor_cost_fraction=floor_cost_fraction,
            hard_floor=self.price_floor,
            hard_ceiling=self.price_ceiling,
        )

    # ------------------------------------------------------------------
    # End-of-life pool
    # ------------------------------------------------------------------

    def add_to_eol_pool(self, product_units, mineral_tons=None):
        if product_units <= 0:
            return
        if mineral_tons is None:
            if self.manufacturers:
                avg_intensity = (
                    sum(m.mineral_intensity for m in self.manufacturers)
                    / len(self.manufacturers)
                )
            else:
                avg_intensity = self.config.get("manufacturer_mineral_intensity", 0.008)
            mineral_tons = product_units * avg_intensity
        if mineral_tons <= 0:
            return
        future_step = self.current_step + self.product_lifetime
        self.end_of_life_pool[future_step] = (
            self.end_of_life_pool.get(future_step, 0) + mineral_tons
        )

    def get_eol_materials(self):
        return self.end_of_life_pool.get(self.current_step, 0)

    def collect_eol(self, rate):
        if rate <= 0:
            return 0.0
        requested = self._eol_initial_this_step * rate
        return self.collect_eol_tons(requested)

    def collect_eol_tons(self, tons):
        """Collect a fixed tonnage from this step's EOL bucket.

        Caller is responsible for sizing the request (typically a fair
        share of the snapshot, possibly capped at facility capacity).
        Returns the actual tonnage collected, which may be less than
        requested if the live bucket has already been drained by other
        recyclers earlier in the same step.
        """
        if tons <= 0:
            return 0.0
        remaining = self.end_of_life_pool.get(self.current_step, 0.0)
        actual = min(tons, remaining)
        if actual <= 0:
            return 0.0
        self.end_of_life_pool[self.current_step] = max(0.0, remaining - actual)
        return actual

    def remove_from_eol_pool(self, mineral_tons):
        current = self.end_of_life_pool.get(self.current_step, 0)
        self.end_of_life_pool[self.current_step] = max(0, current - mineral_tons)

    # ------------------------------------------------------------------
    # Data collection
    # ------------------------------------------------------------------

    def _setup_data_collector(self):
        self.datacollector = DataCollector(
            model_reporters={
                "Step": lambda m: m.current_step,
                "Global_Price": lambda m: m.current_price,
                "Marginal_Cost": lambda m: m._marginal_cost,
                "Cheapest_Active_Cost": lambda m: m._cheapest_cost,
                "Total_Processor_Inventory": lambda m: sum(a.inventory for a in m.processors),
                "Total_Mine_Output": lambda m: sum(a.available_production_this_step for a in m.mines),
                "Total_Recycled_Supply": lambda m: sum(a.recycled_this_step for a in m.recyclers),
                "Disrupted_Mines_Count": lambda m: sum(1 for a in m.mines if a.disruption_counter > 0),
                "Mothballed_Mines_Count": lambda m: sum(1 for a in m.mines if a.mothballed),
                "Total_Consumer_Demand_Units": lambda m: sum(a.current_demand for a in m.consumers),
                "Fulfilled_Demand_Units": lambda m: sum(a.fulfilled_demand for a in m.consumers),
                "Unfulfilled_Demand_Units": lambda m: sum(a.unfulfilled_demand for a in m.consumers),
                "Avg_Manufacturer_Intensity": lambda m: (
                    np.mean([a.mineral_intensity for a in m.manufacturers])
                    if m.manufacturers else 0
                ),
                "Total_Reserves": lambda m: sum(a.reserves for a in m.mines),
                "Embargoed_Mines_Count": lambda m: sum(
                    1 for a in m.mines if m.is_embargoed(a.jurisdiction)
                ),
                "Total_Embargoed_Production": lambda m: sum(
                    a.embargoed_production_this_step for a in m.mines
                ),
                "Total_Domestic_Stockpile": lambda m: sum(a.domestic_stockpile for a in m.mines),
                "Closed_Chokepoints_Count": lambda m: len(m.closed_chokepoints),
                # Mineral-tonnes in transit. Sums ore/processed/recycled
                # quantity (already in mineral tonnes) plus the embedded
                # mineral content of finished-goods shipments. Avoids the
                # apples-and-oranges mix of summing tons + product units.
                "Total_In_Transit_Tons": lambda m: sum(
                    (s['quantity'] if s['material'] in ('ore', 'processed', 'recycled')
                     else s.get('mineral', 0.0))
                    for t in m.transport_agents for s in t.in_transit
                ),
                # Product-unit shipments in transit (manufacturer -> retailer).
                "Total_Product_Units_In_Transit": lambda m: sum(
                    s['quantity'] for t in m.transport_agents
                    for s in t.in_transit if s['material'] == 'product'
                ),
            }
        )

    # ------------------------------------------------------------------
    # Run loop
    # ------------------------------------------------------------------

    def run_model(self, n_steps=None):
        if n_steps is None:
            n_steps = self.config.get("n_steps", 200)
        print(f"\nRunning {self.mineral_type} supply chain model for {n_steps} steps...")
        for i in range(n_steps):
            self.step()
            if (i + 1) % 50 == 0:
                print(f"  Step {i + 1}/{n_steps} - Price: ${self.current_price:,.0f}/ton")
        print("Simulation complete!")

    def get_model_data(self):
        return self.datacollector.get_model_vars_dataframe()
