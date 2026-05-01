# Implementation Roadmap

## Overview
This document outlines the step-by-step implementation plan for the Critical Minerals Supply Chain ABM, including estimated complexity and dependencies between components.

## Phase 1: Foundation (4 files)

### 1.1 Data Loading Module
**File**: `src/data/data_loader.py`
**Dependencies**: pandas, numpy
**Complexity**: Low
**Key Functions**:
- `load_usgs_data(mineral_type)` - Load and filter USGS CSV
- `get_producing_countries(mineral_type)` - List countries with production > 0
- `calculate_market_shares(mineral_type)` - Compute % of global production
- `derive_mine_parameters(country, mineral_type)` - Generate mine attributes

**Validation**:
- Ensure production totals match USGS data
- Verify all non-zero producers are included
- Check for missing/NaN values

### 1.2 Configuration Module
**Files**: `src/config/lithium_config.py`, `nickel_config.py`, `platinum_config.py`
**Dependencies**: None
**Complexity**: Low
**Structure**: Python dictionaries with all model parameters

### 1.3 Market Mechanism
**File**: `src/model/market_mechanism.py`
**Dependencies**: None (pure Python logic)
**Complexity**: Low
**Key Functions**:
- `update_price(current_price, inventory_ratio, bounds)` - Price dynamics
- `calculate_inventory_ratio(processor_inventory, demand)` - Supply/demand ratio
- `check_geopolitical_event(probability, jurisdictions, random_state)` - Event trigger

### 1.4 Project Structure
**Action**: Create all directories
```bash
mkdir -p src/{agents,model,data,visualization,config}
mkdir -p outputs
touch src/__init__.py
touch src/{agents,model,data,visualization,config}/__init__.py
```

## Phase 2: Agent Implementation (7 files)

### 2.1 MineAgent
**File**: `src/agents/mine_agent.py`
**Dependencies**: mesa.Agent
**Complexity**: Medium
**Key Methods**:
- `__init__(unique_id, model, jurisdiction, reserves, production_capacity, extraction_cost, ore_grade)`
- `step()` - Main behavior: check profitability, produce, handle disruptions
- `produce()` - Calculate output based on reserves and capacity
- `check_disruption()` - 2% random disruption chance
- `evaluate_market_conditions()` - Decide to shutdown/restart

**Integration Points**:
- Reads model.current_price
- Offers production to ProcessorAgents
- Affected by model.geopolitical_events

### 2.2 ProcessorAgent
**File**: `src/agents/processor_agent.py`
**Dependencies**: mesa.Agent
**Complexity**: Medium
**Key Methods**:
- `__init__(unique_id, model, conversion_efficiency, energy_cost, capacity)`
- `step()` - Buy ore, convert, manage inventory
- `rank_suppliers()` - Sort mines by total cost
- `purchase_ore(mine, quantity)` - Transaction logic
- `convert_ore(quantity)` - Apply conversion efficiency
- `sell_to_manufacturers(quantity)` - Fulfill orders

**Integration Points**:
- Buys from MineAgents
- Sells to ManufacturerAgents
- Inventory feeds into market price mechanism

### 2.3 TransportAgent
**File**: `src/agents/transport_agent.py`
**Dependencies**: mesa.Agent, collections.deque
**Complexity**: Medium-High
**Key Methods**:
- `__init__(unique_id, model, mode, cost_per_unit, lead_time, capacity)`
- `step()` - Process queue, deliver materials
- `accept_shipment(origin, destination, quantity, material_type)` - Add to queue
- `deliver()` - Complete shipments that arrived
- `calculate_cost(quantity, distance_factor)` - Compute transport cost
- `apply_disruption(jurisdiction)` - Block routes

**Data Structure**:
```python
in_transit = [
    {"material": ore, "quantity": 1000, "origin": mine_id, 
     "destination": processor_id, "arrival_step": 15},
    ...
]
```

**Integration Points**:
- Links MineAgents to ProcessorAgents
- Affected by geopolitical events

### 2.4 ManufacturerAgent
**File**: `src/agents/manufacturer_agent.py`
**Dependencies**: mesa.Agent
**Complexity**: Medium
**Key Methods**:
- `__init__(unique_id, model, mineral_intensity, production_capacity)`
- `step()` - Order materials, produce, track prices
- `manage_inventory()` - Reorder logic
- `produce_goods()` - Convert minerals to products
- `update_substitution()` - Reduce mineral_intensity if prices high
- `check_price_history()` - Track consecutive high-price steps

**Substitution Logic**:
```python
if high_price_counter > 10:
    substitution_investment += 0.01
    mineral_intensity *= (1 - 0.05 * substitution_investment)
    high_price_counter = 0
```

**Integration Points**:
- Buys from ProcessorAgents
- Sells to RetailerAgents
- Drives substitution effect

### 2.5 RetailerAgent
**File**: `src/agents/retailer_agent.py`
**Dependencies**: mesa.Agent
**Complexity**: Low-Medium
**Key Methods**:
- `__init__(unique_id, model, reorder_point, order_quantity)`
- `step()` - Sell to consumers, check inventory, reorder
- `sell(quantity)` - Reduce inventory
- `check_reorder()` - (s, Q) policy
- `place_order()` - Order from manufacturers
- `receive_shipment(quantity)` - Add to inventory

**Integration Points**:
- Buys from ManufacturerAgents
- Sells to ConsumerAgents
- Tracks stockouts

### 2.6 ConsumerAgent
**File**: `src/agents/consumer_agent.py`
**Dependencies**: mesa.Agent, numpy (for log calculation)
**Complexity**: Low-Medium
**Key Methods**:
- `__init__(unique_id, model, base_demand, price_sensitivity)`
- `step()` - Calculate demand, attempt purchase, track EOL
- `calculate_demand()` - Price elasticity formula
- `purchase(retailers)` - Try to buy from available retailers
- `contribute_to_eol_pool()` - Add to model.end_of_life_pool

**Demand Formula**:
```python
price_effect = price_sensitivity * np.log(current_price / initial_price)
current_demand = base_demand * (1 + price_effect)
```

**Integration Points**:
- Buys from RetailerAgents
- Contributes to end_of_life_pool
- Drives overall demand

### 2.7 RecyclingAgent
**File**: `src/agents/recycling_agent.py`
**Dependencies**: mesa.Agent
**Complexity**: Medium
**Key Methods**:
- `__init__(unique_id, model, collection_rate, recovery_efficiency, processing_cost)`
- `step()` - Collect from EOL pool, process, sell
- `collect_eol_materials()` - Retrieve from pool[step-25]
- `process_materials(quantity)` - Apply recovery efficiency
- `evaluate_profitability()` - Check if worth selling
- `sell_to_processors(quantity)` - Inject recovered materials

**Integration Points**:
- Reads model.end_of_life_pool
- Sells to ProcessorAgents
- Tracks recycled_supply metric

## Phase 3: Model Assembly (2 files)

### 3.1 Supply Chain Model
**File**: `src/model/supply_chain_model.py`
**Dependencies**: mesa.Model, mesa.time.RandomActivation, mesa.datacollection.DataCollector
**Complexity**: High
**Key Methods**:
- `__init__(config)` - Initialize from config dict
- `create_agents()` - Instantiate all agents from USGS data
- `step()` - Execute one time step
- `update_price()` - Call market mechanism
- `trigger_geopolitical_event()` - Random event logic
- `run_model(n_steps)` - Execute simulation
- `get_model_data()` - Export DataCollector results

**Step Sequence**:
```python
def step(self):
    # 1. Check for geopolitical events
    self.trigger_geopolitical_event()
    
    # 2. Activate all agents (random order)
    self.schedule.step()
    
    # 3. Update global price
    self.update_price()
    
    # 4. Collect data
    self.datacollector.collect(self)
    
    # 5. Increment step counter
    self.current_step += 1
```

**DataCollector Configuration**:
```python
model_reporters = {
    "Global_Price": lambda m: m.current_price,
    "Total_Processor_Inventory": lambda m: sum(a.inventory for a in m.processors),
    "Total_Mine_Output": lambda m: sum(a.production_this_step for a in m.mines),
    "Total_Recycled_Supply": lambda m: sum(a.recycled_this_step for a in m.recyclers),
    "Disrupted_Mines_Count": lambda m: sum(1 for a in m.mines if not a.operational),
    "Total_Consumer_Demand": lambda m: sum(a.current_demand for a in m.consumers),
    "Fulfilled_Demand": lambda m: sum(a.fulfilled_demand for a in m.consumers),
    "Unfulfilled_Demand": lambda m: sum(a.unfulfilled_demand for a in m.consumers),
    "Avg_Manufacturer_Intensity": lambda m: np.mean([a.mineral_intensity for a in m.manufacturers]),
}
```

## Phase 4: Visualization & Execution (2 files)

### 4.1 Visualizer
**File**: `src/visualization/visualizer.py`
**Dependencies**: matplotlib, seaborn, pandas
**Complexity**: Medium
**Key Functions**:
- `plot_supply_chain_analysis(model_data, config, output_path)` - Main plotting function
- `create_figure_layout()` - Setup 2x3 subplot grid
- `plot_price(ax, data)` - Subplot [0,0]
- `plot_inventory(ax, data)` - Subplot [0,1]
- `plot_supply_comparison(ax, data)` - Subplot [0,2]
- `plot_demand(ax, data)` - Subplot [1,0]
- `plot_disruptions(ax, data)` - Subplot [1,1]
- `plot_substitution(ax, data)` - Subplot [1,2]
- `apply_styling(fig)` - Consistent theme

**Output**: High-resolution PNG files in `outputs/` directory

### 4.2 Main Runner
**File**: `run_simulation.py`
**Dependencies**: argparse, all src modules
**Complexity**: Low-Medium
**Key Functions**:
- `parse_arguments()` - CLI argument parsing
- `run_single_mineral(mineral_type, n_steps, output_dir)` - Execute one scenario
- `run_all_minerals(n_steps, output_dir)` - Execute all three
- `save_results(model, output_dir)` - Export data and plots
- `print_summary_statistics(model)` - Console output

**CLI Interface**:
```bash
python run_simulation.py --mineral lithium --steps 200
python run_simulation.py --all --steps 300 --output-dir results/
python run_simulation.py --mineral nickel --geo-prob 0.02 --seed 123
```

## Implementation Order

### Week 1: Foundation
1. Create directory structure
2. Implement data_loader.py
3. Create configuration files
4. Implement market_mechanism.py
5. Write unit tests for data loading

### Week 2: Core Agents
6. Implement MineAgent
7. Implement ProcessorAgent
8. Test Mine → Processor flow
9. Implement TransportAgent
10. Test with transport delays

### Week 3: Downstream Agents
11. Implement ManufacturerAgent
12. Implement RetailerAgent
13. Implement ConsumerAgent
14. Test full forward flow (Mine → Consumer)

### Week 4: Circular Economy & Model
15. Implement RecyclingAgent
16. Test recycling loop with EOL pool
17. Implement SupplyChainModel
18. Integrate all agents into model
19. Test geopolitical events

### Week 5: Visualization & Polish
20. Implement visualizer.py
21. Create run_simulation.py
22. Generate test plots for all minerals
23. Calibrate parameters
24. Write documentation

## Testing Strategy

### Unit Tests
- Each agent class: Test initialization and basic methods
- Data loader: Verify USGS data parsing correctness
- Market mechanism: Test price update logic with known inputs

### Integration Tests
- Mine → Processor → Manufacturer flow
- Transport delays work correctly
- Recycling loop with 25-step lag
- Geopolitical event disruptions

### System Tests
- Run full 200-step simulation for each mineral
- Verify data collection completeness
- Check visualization generation
- Validate against USGS baseline data

### Validation Tests
- Production totals ≈ USGS data (±10%)
- Price stability without shocks
- Recycling contribution 10-20% by step 100
- Geopolitical events cause 20-50% price spikes
- Substitution reduces intensity 20-30%

## Key Dependencies Between Components

```
data_loader.py → config files → supply_chain_model.py
                              ↓
market_mechanism.py → supply_chain_model.py
                              ↓
All agent files → supply_chain_model.py
                              ↓
supply_chain_model.py → visualizer.py
                              ↓
visualizer.py → run_simulation.py
```

## Critical Path Items

1. **Data Loader** - Must be correct; all agents depend on it
2. **MineAgent** - Foundation of supply
3. **ProcessorAgent** - Central hub for inventory
4. **Market Mechanism** - Drives all economic behavior
5. **SupplyChainModel** - Integrates everything

## Risk Mitigation

| Risk | Mitigation |
|------|-----------|
| USGS data parsing errors | Extensive validation, unit tests on sample data |
| Agent interaction bugs | Incremental testing, start with 2 agents before adding more |
| Performance issues | Profile code, optimize bottlenecks, limit agent counts |
| Parameter calibration | Sensitivity analysis, comparison to literature |
| Mesa 2.x API changes | Pin version in requirements.txt, check docs |

## Definition of Done

Each component is "done" when:
1. Code is written and documented
2. Unit tests pass (if applicable)
3. Integration tests pass (if applicable)
4. Code review completed (if team)
5. Merged into main branch

The project is "done" when:
1. All 20 components implemented
2. All three minerals run successfully
3. Visualizations generated correctly
4. Documentation complete
5. README has usage examples

---
**Last Updated**: 2026-05-01
**Status**: Ready to Begin Implementation
