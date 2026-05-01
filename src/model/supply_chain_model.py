"""
Main supply chain model integrating all agents.
Uses Mesa 2.x syntax with RandomActivation and DataCollector.
"""

from mesa import Model
from mesa.time import RandomActivation
from mesa.datacollection import DataCollector
import numpy as np
import random

# Import agents
from ..agents.mine_agent import MineAgent
from ..agents.processor_agent import ProcessorAgent
from ..agents.transport_agent import TransportAgent
from ..agents.manufacturer_agent import ManufacturerAgent
from ..agents.retailer_agent import RetailerAgent
from ..agents.consumer_agent import ConsumerAgent
from ..agents.recycling_agent import RecyclingAgent

# Import utilities
from ..data.data_loader import load_mineral_data
from .market_mechanism import (
    update_price, check_geopolitical_event,
    select_affected_jurisdiction, calculate_disruption_duration
)


class MineralSupplyChainModel(Model):
    """Agent-based model of critical minerals supply chain."""
    
    def __init__(self, config, csv_path="USGS_CMM.csv"):
        """Initialize the supply chain model.
        
        Args:
            config: Configuration dictionary
            csv_path: Path to USGS data file
        """
        super().__init__()
        
        self.config = config
        self.mineral_type = config["mineral_type"]
        
        # Initialize random seed
        seed = config.get("random_seed", 42)
        random.seed(seed)
        np.random.seed(seed)
        self.random_state = random.Random(seed)
        
        # Price state
        self.initial_price = config["initial_price"]
        self.current_price = self.initial_price
        self.price_floor = config["price_floor"]
        self.price_ceiling = config["price_ceiling"]
        
        # End-of-life pool for recycling (dictionary: step -> quantity)
        self.end_of_life_pool = {}
        self.product_lifetime = config.get("product_lifetime_steps", 25)
        
        # Geopolitical event tracking
        self.active_disruptions = {}  # jurisdiction -> remaining_steps
        
        # Agent lists for easy access
        self.mines = []
        self.processors = []
        self.transport_agents = []
        self.manufacturers = []
        self.retailers = []
        self.consumers = []
        self.recyclers = []
        
        # Scheduler
        self.schedule = RandomActivation(self)
        
        # Load data and create agents
        self._load_data_and_create_agents(csv_path)
        
        # Setup data collector
        self._setup_data_collector()
        
        # Tracking
        self.current_step = 0
    
    def _load_data_and_create_agents(self, csv_path):
        """Load USGS data and create all agents."""
        # Load mineral-specific data
        mines_data, demand_data = load_mineral_data(self.mineral_type, csv_path)

        # Resolve baseline annual mineral demand (tonnes/year), then derive
        # per-step mineral and product demand once for use across creators.
        self._resolve_baseline_demand(demand_data)

        # Create mines from USGS data
        self._create_mines(mines_data)

        # Create processors
        self._create_processors()

        # Create transport agents
        self._create_transport()

        # Create manufacturers
        self._create_manufacturers()

        # Create retailers
        self._create_retailers()

        # Create consumers
        self._create_consumers()

        # Create recyclers
        self._create_recyclers()

    def _resolve_baseline_demand(self, demand_data):
        """Resolve baseline annual mineral demand (tonnes/year).

        USGS demand columns are in kilotonnes/year while production columns
        are in tonnes/year, so we rescale kt -> t. If the mineral has no
        demand column (e.g., Platinum), fall back to
        config['default_annual_demand_tons'].
        """
        baseline_demand_tons_per_year = 0.0
        if demand_data:
            for key, value in demand_data.items():
                if '2024' in str(key) and value > 0:
                    baseline_demand_tons_per_year = value * 1000.0
                    print(f"Using USGS 2024 demand: {baseline_demand_tons_per_year:,.0f} tons/year")
                    break

        if baseline_demand_tons_per_year <= 0:
            baseline_demand_tons_per_year = self.config.get(
                'default_annual_demand_tons', 100000.0
            )
            print(f"No USGS 2024 demand found; using config default: "
                  f"{baseline_demand_tons_per_year:,.0f} tons/year")

        steps_per_year = self.config.get('steps_per_year', 52)
        mineral_intensity = self.config.get('manufacturer_mineral_intensity', 0.08)

        self.baseline_mineral_demand_tons_per_year = baseline_demand_tons_per_year
        self.baseline_mineral_demand_per_step = (
            baseline_demand_tons_per_year / steps_per_year
        )
        self.baseline_product_demand_per_step = (
            self.baseline_mineral_demand_per_step / mineral_intensity
        )

        print(f"Mineral demand: {self.baseline_mineral_demand_per_step:,.2f} tons/step "
              f"-> Product demand: {self.baseline_product_demand_per_step:,.2f} units/step")
    
    def _create_mines(self, mines_data):
        """Create mine agents from USGS data.

        Two rescalings happen here:
        1. usgs_units_to_tons: USGS_CMM.csv reports Platinum production and
           reserves in kilograms while Lithium and Nickel use tonnes. The
           per-mineral config supplies the conversion factor (default 1.0).
        2. steps_per_year: production_capacity is an annual flow in the
           source data and a per-step capacity in the agent.
        Reserves are stocks, so they only get rescaled by units_to_tons.
        """
        steps_per_year = self.config.get('steps_per_year', 52)
        units_to_tons = self.config.get('usgs_units_to_tons', 1.0)

        for i, mine_params in enumerate(mines_data):
            mine = MineAgent(
                unique_id=self.next_id(),
                model=self,
                jurisdiction=mine_params['jurisdiction'],
                ore_grade=mine_params['ore_grade'],
                production_capacity=mine_params['production_capacity'] * units_to_tons / steps_per_year,
                extraction_cost=mine_params['extraction_cost'],
                reserves=mine_params['reserves'] * units_to_tons
            )
            self.schedule.add(mine)
            self.mines.append(mine)

        print(f"Created {len(self.mines)} mines")
    
    def _create_processors(self):
        """Create processor agents."""
        n_processors = self.config.get("n_processors", 5)
        conversion_eff = self.config.get("processor_conversion_efficiency", 0.80)
        energy_cost = self.config.get("processor_energy_cost", 1500)
        
        # Estimate capacity based on total mine production
        total_mine_capacity = sum(m.production_capacity for m in self.mines)
        processor_capacity = total_mine_capacity / n_processors * 1.2  # 20% buffer
        
        for i in range(n_processors):
            processor = ProcessorAgent(
                unique_id=self.next_id(),
                model=self,
                conversion_efficiency=conversion_eff,
                energy_cost=energy_cost,
                capacity=processor_capacity
            )
            self.schedule.add(processor)
            self.processors.append(processor)
        
        print(f"Created {len(self.processors)} processors")
    
    def _create_transport(self):
        """Create transport agents."""
        n_transport = self.config.get("n_transport", 10)
        
        # Create mix of transport modes
        modes = ['ship', 'rail', 'truck']
        costs = {
            'ship': self.config.get("transport_cost_ship", 10),
            'rail': self.config.get("transport_cost_rail", 25),
            'truck': self.config.get("transport_cost_truck", 50)
        }
        lead_times = {
            'ship': self.config.get("transport_lead_time_ship", 7),
            'rail': self.config.get("transport_lead_time_rail", 4),
            'truck': self.config.get("transport_lead_time_truck", 2)
        }
        
        for i in range(n_transport):
            mode = modes[i % len(modes)]
            transport = TransportAgent(
                unique_id=self.next_id(),
                model=self,
                mode=mode,
                cost_per_unit=costs[mode],
                lead_time=lead_times[mode],
                capacity=10000  # Large capacity (simplified)
            )
            self.schedule.add(transport)
            self.transport_agents.append(transport)
        
        print(f"Created {len(self.transport_agents)} transport agents")
    
    def _create_manufacturers(self):
        """Create manufacturer agents.

        Capacity is sized from the baseline product demand per step (with a
        small headroom multiplier) rather than from inverted processor
        capacity. The previous formula divided by mineral_intensity, which
        produced absurdly large capacity for low-intensity minerals (Pt).
        """
        n_manufacturers = self.config.get("n_manufacturers", 8)
        mineral_intensity = self.config.get("manufacturer_mineral_intensity", 0.08)
        capacity_headroom = self.config.get("manufacturer_capacity_headroom", 1.5)

        manufacturer_capacity = (
            self.baseline_product_demand_per_step * capacity_headroom / n_manufacturers
        )

        for i in range(n_manufacturers):
            manufacturer = ManufacturerAgent(
                unique_id=self.next_id(),
                model=self,
                mineral_intensity=mineral_intensity,
                production_capacity=manufacturer_capacity
            )
            self.schedule.add(manufacturer)
            self.manufacturers.append(manufacturer)

        print(f"Created {len(self.manufacturers)} manufacturers "
              f"(capacity ~{manufacturer_capacity:,.2f} units/step each)")
    
    def _create_retailers(self):
        """Create retailer agents.

        Reorder point and order quantity are sized from per-retailer baseline
        product demand, not from inflated manufacturer capacity.
        """
        n_retailers = self.config.get("n_retailers", 12)

        avg_demand_per_retailer = self.baseline_product_demand_per_step / n_retailers

        reorder_multiplier = self.config.get("retailer_reorder_point_multiplier", 2.0)
        order_multiplier = self.config.get("retailer_order_quantity_multiplier", 3.0)

        reorder_point = avg_demand_per_retailer * reorder_multiplier
        order_quantity = avg_demand_per_retailer * order_multiplier

        for i in range(n_retailers):
            retailer = RetailerAgent(
                unique_id=self.next_id(),
                model=self,
                reorder_point=reorder_point,
                order_quantity=order_quantity
            )
            self.schedule.add(retailer)
            self.retailers.append(retailer)

        print(f"Created {len(self.retailers)} retailers "
              f"(reorder_point={reorder_point:,.2f}, Q={order_quantity:,.2f})")
    
    def _create_consumers(self):
        """Create consumer agents using the precomputed baseline demand."""
        n_consumers = self.config.get("n_consumers", 100)
        price_sensitivity = self.config.get("consumer_price_sensitivity", -0.8)

        base_demand_per_consumer = (
            self.baseline_product_demand_per_step / n_consumers
        )

        print(f"Per consumer: {base_demand_per_consumer:,.4f} units/step")

        for i in range(n_consumers):
            consumer = ConsumerAgent(
                unique_id=self.next_id(),
                model=self,
                base_demand=base_demand_per_consumer,
                price_sensitivity=price_sensitivity
            )
            self.schedule.add(consumer)
            self.consumers.append(consumer)

        print(f"Created {len(self.consumers)} consumers")
    
    def _create_recyclers(self):
        """Create recycling agents."""
        n_recyclers = self.config.get("n_recyclers", 3)
        collection_rate = self.config.get("collection_rate", 0.30)
        recovery_efficiency = self.config.get("recovery_efficiency", 0.70)
        processing_cost = self.config.get("recycling_processing_cost", 5000)
        
        for i in range(n_recyclers):
            recycler = RecyclingAgent(
                unique_id=self.next_id(),
                model=self,
                collection_rate=collection_rate / n_recyclers,  # Split collection
                recovery_efficiency=recovery_efficiency,
                processing_cost=processing_cost
            )
            self.schedule.add(recycler)
            self.recyclers.append(recycler)
        
        print(f"Created {len(self.recyclers)} recyclers")
    
    def _setup_data_collector(self):
        """Setup Mesa DataCollector for tracking metrics."""
        self.datacollector = DataCollector(
            model_reporters={
                "Step": lambda m: m.current_step,
                "Global_Price": lambda m: m.current_price,
                "Total_Processor_Inventory": lambda m: sum(a.inventory for a in m.processors),
                "Total_Mine_Output": lambda m: sum(a.production_this_step for a in m.mines),
                "Total_Recycled_Supply": lambda m: sum(a.recycled_this_step for a in m.recyclers),
                "Disrupted_Mines_Count": lambda m: sum(1 for a in m.mines if not a.operational),
                "Total_Consumer_Demand": lambda m: sum(a.current_demand for a in m.consumers),
                "Fulfilled_Demand": lambda m: sum(a.fulfilled_demand for a in m.consumers),
                "Unfulfilled_Demand": lambda m: sum(a.unfulfilled_demand for a in m.consumers),
                "Avg_Manufacturer_Intensity": lambda m: np.mean([a.mineral_intensity for a in m.manufacturers]) if m.manufacturers else 0,
                "Total_Reserves": lambda m: sum(a.reserves for a in m.mines),
            }
        )
    
    def step(self):
        """Execute one time step of the model."""
        # 1. Check for geopolitical events
        self._check_geopolitical_events()
        
        # 2. Activate all agents in random order
        self.schedule.step()
        
        # 3. Update global price based on supply/demand
        self._update_price()
        
        # 4. Collect data
        self.datacollector.collect(self)
        
        # 5. Increment step counter
        self.current_step += 1
    
    def _check_geopolitical_events(self):
        """Check for and handle geopolitical events."""
        # Decrement active disruptions
        expired = []
        for jurisdiction, remaining in self.active_disruptions.items():
            if remaining <= 1:
                expired.append(jurisdiction)
            else:
                self.active_disruptions[jurisdiction] -= 1
        
        # Remove expired disruptions
        for jurisdiction in expired:
            del self.active_disruptions[jurisdiction]
        
        # Check for new event
        geo_prob = self.config.get("geopolitical_event_probability", 0.01)
        if check_geopolitical_event(geo_prob, self.random_state):
            self._trigger_geopolitical_event()
    
    def _trigger_geopolitical_event(self):
        """Trigger a geopolitical disruption event."""
        # Get all jurisdictions with mines
        jurisdictions = list(set(mine.jurisdiction for mine in self.mines))
        
        if not jurisdictions:
            return
        
        # Select random jurisdiction
        affected = select_affected_jurisdiction(jurisdictions, self.random_state)
        
        # Calculate duration
        min_dur = self.config.get("geopolitical_duration_min", 5)
        max_dur = self.config.get("geopolitical_duration_max", 15)
        duration = calculate_disruption_duration(min_dur, max_dur, self.random_state)
        
        # Apply disruption
        self.active_disruptions[affected] = duration
        
        # Disrupt all mines in jurisdiction
        for mine in self.mines:
            if mine.jurisdiction == affected:
                mine.apply_geopolitical_disruption(duration)
        
        # Disrupt transport (simplified: just note it)
        for transport in self.transport_agents:
            transport.apply_disruption(affected, duration)
        
        print(f"Geopolitical event! {affected} disrupted for {duration} steps")
    
    def _update_price(self):
        """Update global price based on weeks-of-inventory at processors.

        Uses raw inventory rather than inventory net of safety stock; the old
        net-of-safety-stock signal was almost always zero and produced a
        permanent shortage signal.
        """
        total_inventory = sum(p.inventory for p in self.processors)

        # Convert current product demand to mineral tons using live
        # manufacturer intensity (so substitution feeds back into price).
        total_product_demand = sum(c.current_demand for c in self.consumers)
        if self.manufacturers:
            avg_intensity = sum(m.mineral_intensity for m in self.manufacturers) / len(self.manufacturers)
        else:
            avg_intensity = self.config.get("manufacturer_mineral_intensity", 0.08)
        mineral_demand = max(total_product_demand * avg_intensity, 1e-9)

        shortage_weeks = self.config.get("price_shortage_weeks", 4.0)
        surplus_weeks = self.config.get("price_surplus_weeks", 12.0)

        self.current_price = update_price(
            self.current_price,
            total_inventory,
            mineral_demand,
            self.price_floor,
            self.price_ceiling,
            shortage_weeks=shortage_weeks,
            surplus_weeks=surplus_weeks,
        )
    
    def add_to_eol_pool(self, product_units):
        """Add sold products to the EOL pool, stored as contained mineral tons.

        We multiply by the manufacturer intensity at the moment of sale so
        recyclers later collect the correct mineral mass even if intensity
        shifts due to substitution between sale and end-of-life.
        """
        if product_units <= 0:
            return

        if self.manufacturers:
            avg_intensity = sum(m.mineral_intensity for m in self.manufacturers) / len(self.manufacturers)
        else:
            avg_intensity = self.config.get("manufacturer_mineral_intensity", 0.08)

        mineral_tons = product_units * avg_intensity
        future_step = self.current_step + self.product_lifetime
        self.end_of_life_pool[future_step] = (
            self.end_of_life_pool.get(future_step, 0) + mineral_tons
        )

    def get_eol_materials(self):
        """Return contained-mineral tons due for collection this step."""
        return self.end_of_life_pool.get(self.current_step, 0)

    def remove_from_eol_pool(self, mineral_tons):
        """Remove collected mineral tons from this step's EOL bucket."""
        current = self.end_of_life_pool.get(self.current_step, 0)
        self.end_of_life_pool[self.current_step] = max(0, current - mineral_tons)
    
    def run_model(self, n_steps=None):
        """Run the model for a specified number of steps.
        
        Args:
            n_steps: Number of steps to run (uses config if not specified)
        """
        if n_steps is None:
            n_steps = self.config.get("n_steps", 200)
        
        print(f"\nRunning {self.mineral_type} supply chain model for {n_steps} steps...")
        
        for i in range(n_steps):
            self.step()
            
            # Print progress every 50 steps
            if (i + 1) % 50 == 0:
                print(f"  Step {i + 1}/{n_steps} - Price: ${self.current_price:,.0f}/ton")
        
        print(f"Simulation complete!")
    
    def get_model_data(self):
        """Get collected data as a pandas DataFrame.
        
        Returns:
            DataFrame with all collected metrics
        """
        return self.datacollector.get_model_vars_dataframe()
