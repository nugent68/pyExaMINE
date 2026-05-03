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
from mesa.time import BaseScheduler
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
    update_price, check_geopolitical_event,
    select_affected_jurisdiction, calculate_disruption_duration
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

        # Agent lists per tier
        self.mines = []
        self.recyclers = []
        self.processors = []
        self.manufacturers = []
        self.retailers = []
        self.consumers = []
        self.transport_agents = []

        self.schedule = BaseScheduler(self)
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
        """Set baseline_mineral_demand_per_step from data + config fallback."""
        baseline = 0.0
        if demand_data and '2024' in demand_data and demand_data['2024'] > 0:
            baseline = float(demand_data['2024'])
            print(f"Using 2024 demand: {baseline:,.0f} tons/year")
        if baseline <= 0:
            baseline = float(self.config.get('default_annual_demand_tons', 100000.0))
            print(f"No 2024 demand row; using config default: {baseline:,.0f} tons/year")

        steps_per_year = self.config.get('steps_per_year', 52)
        intensity = self.config.get('manufacturer_mineral_intensity', 0.008)

        self.baseline_mineral_demand_tons_per_year = baseline
        self.baseline_mineral_demand_per_step = baseline / steps_per_year
        self.baseline_product_demand_per_step = self.baseline_mineral_demand_per_step / intensity

        print(
            f"Mineral demand: {self.baseline_mineral_demand_per_step:,.2f} t/step "
            f"-> Product demand: {self.baseline_product_demand_per_step:,.2f} units/step"
        )

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
            self.schedule.add(mine)
            self.mines.append(mine)
        print(f"Created {len(self.mines)} mines across "
              f"{len({a.country for a in self.mines})} countries")

    def _create_recyclers(self, recyclers_data):
        """Create per-facility recyclers.

        Each recycler claims a fraction of the EOL bucket sized to its
        share of total recycling capacity. Recovery efficiency comes from
        the per-facility row.
        """
        total_capacity = sum(r['capacity'] for r in recyclers_data) or 1.0
        # Aggregate collection rate across all facilities (config knob;
        # default 30% of available EOL across all recyclers per step).
        agg_collection = self.config.get("collection_rate", 0.30)

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
            )
            self.schedule.add(recycler)
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
            self.schedule.add(processor)
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
            self.schedule.add(mfr)
            self.manufacturers.append(mfr)
        print(f"Created {len(self.manufacturers)} manufacturer aggregates "
              f"(one per producing country)")

    def _create_retailers(self, country_shares):
        """One retailer per consumer country, sized to that country's demand share."""
        reorder_mult = self.config.get("retailer_reorder_point_multiplier", 2.0)
        order_mult = self.config.get("retailer_order_quantity_multiplier", 3.0)

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
            self.schedule.add(retailer)
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
            self.schedule.add(consumer)
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
                self.schedule.add(t)
                self.transport_agents.append(t)
        print(f"Created {len(self.transport_agents)} transport agents across "
              f"{len({a.country for a in self.transport_agents})} countries")

    # ------------------------------------------------------------------
    # Routing / dispatch
    # ------------------------------------------------------------------

    def select_transport(self, mode, prefer_country=None):
        """Pick a transport agent of the given mode, preferring a country.

        Returns the chosen agent, or None if the fleet is empty.
        """
        if not self.transport_agents:
            return None
        mode_pool = [t for t in self.transport_agents if t.mode == mode]
        if not mode_pool:
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

        route = select_route_or_fallback(
            origin_country, dest_country, set(self.closed_chokepoints),
        )
        if route is None:
            # Truly unknown country pair -- treat as direct, 1-step truck.
            chokepoints, lead_time, mode = ([], 1, 'truck')
        else:
            chokepoints, lead_time, mode = route

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

        self.schedule.steps += 1
        self.schedule.time += 1

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
        """Tick existing chokepoint closures and start scheduled ones."""
        for cp in list(self.closed_chokepoints):
            end = self.closed_chokepoints[cp]
            if end <= self.current_step + 1:
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

        shortage_ratio = float(self.config.get("price_shortage_ratio", 0.95))
        surplus_ratio = float(self.config.get("price_surplus_ratio", 1.10))

        self.current_price = update_price(
            self.current_price,
            supply_smoothed,
            demand_smoothed,
            self.price_floor,
            self.price_ceiling,
            shortage_ratio=shortage_ratio,
            surplus_ratio=surplus_ratio,
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
        remaining = self.end_of_life_pool.get(self.current_step, 0.0)
        actual = min(requested, remaining)
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
                "Total_In_Transit": lambda m: sum(
                    sum(s['quantity'] for s in t.in_transit)
                    for t in m.transport_agents
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
