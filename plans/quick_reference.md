# Critical Minerals Supply Chain ABM - Quick Reference

## Agent Types Summary

| Agent | Count | Purpose | Key Attributes | Key Behaviors |
|-------|-------|---------|----------------|---------------|
| **MineAgent** | Variable (per USGS data) | Extract raw minerals | jurisdiction, reserves, extraction_cost, ore_grade | Produce if profitable, random disruptions (2%), shutdown if unprofitable |
| **ProcessorAgent** | 3-5 | Convert ore to processed mineral | conversion_efficiency (0.7-0.9), inventory | Buy from cheapest mines, convert, sell to manufacturers |
| **TransportAgent** | Auto-created | Move materials with delays | mode (ship/rail/truck), lead_time, cost | Queue shipments, deliver after delay, can be disrupted |
| **ManufacturerAgent** | 4-8 | Produce goods using minerals | mineral_intensity, substitution_investment | Order materials, produce, reduce intensity if prices high 10+ steps |
| **RetailerAgent** | 6-12 | Manage inventory, sell to consumers | reorder_point, order_quantity | (s,Q) policy, reorder when low |
| **ConsumerAgent** | 50-100 | Generate demand | base_demand, price_sensitivity | Buy from retailers, demand drops if price too high |
| **RecyclingAgent** | 2-3 | Recover minerals from EOL products | collection_rate (0.3), recovery_efficiency (0.7) | Collect from EOL pool (25-step lag), process and sell |

## Key Mechanisms

### Market Price Update
```
inventory_ratio = total_processor_inventory / average_demand
if inventory_ratio < 0.5:  price *= 1.05  (shortage)
if inventory_ratio > 1.5:  price *= 0.95  (oversupply)
price = clamp(price, floor, ceiling)
```

### Geopolitical Events
- **Probability**: 1% per step
- **Duration**: 5-15 steps
- **Effect**: Disrupts all mines and transport in selected jurisdiction

### Recycling Loop
- **Product Lifetime**: 25 steps
- **EOL Pool**: end_of_life_pool[step] = purchases[step]
- **Collection**: recyclers draw from pool[step-25]

### Manufacturer Substitution
- **Trigger**: Price > threshold for 10+ consecutive steps
- **Effect**: Reduce mineral_intensity by 5% per investment cycle
- **Max Reduction**: 30% total

## Mineral-Specific Parameters

| Parameter | Lithium | Nickel | Platinum |
|-----------|---------|--------|----------|
| **Initial Price** ($/ton) | 17,000 | 18,000 | 30,000,000 |
| **Price Floor** | 6,800 | 7,200 | 12,000,000 |
| **Price Ceiling** | 51,000 | 54,000 | 90,000,000 |
| **Avg Ore Grade** | 0.85 | 0.65 | 0.55 |
| **Conversion Efficiency** | 0.80 | 0.75 | 0.70 |
| **Mineral Intensity** (tons/unit) | 0.08 | 0.04 | 0.00005 |
| **Collection Rate** | 0.30 | 0.60 | 0.75 |
| **Recovery Efficiency** | 0.70 | 0.75 | 0.85 |

## Data Sources (USGS_CMM.csv)

### Top Producers by Mineral

**Lithium:**
1. Australia: 92,000 tons/year
2. Chile: 56,000 tons/year
3. China: 62,000 tons/year
4. Argentina: 23,000 tons/year

**Nickel:**
1. Indonesia: 2,600,000 tons/year
2. Philippines: 270,000 tons/year
3. Russia: 200,000 tons/year
4. Australia: 45,000 tons/year

**Platinum:**
1. South Africa: 70,000 kg/year
2. Russia: 0 kg/year (listed)
3. Zimbabwe: 15,000 kg/year
4. Canada: 16,000 kg/year

## Visualization Outputs

### 6-Panel Dashboard (2x3 grid)
1. **Mineral Price Over Time** - Shows volatility and response to shocks
2. **Processor Inventory** - Buffer against supply disruptions
3. **Mine Output vs. Recycled Supply** - Growing circular economy
4. **Fulfilled vs. Unfulfilled Demand** - Market tightness indicator
5. **Disrupted Mines Count** - Correlation with price spikes
6. **Manufacturer Mineral Intensity** - Substitution effect over time

## File Structure

```
pyExaMINE/
├── src/
│   ├── agents/           # 7 agent classes
│   ├── model/            # Main model + market mechanism
│   ├── data/             # USGS data loader
│   ├── visualization/    # Plotting functions
│   └── config/           # Mineral-specific configs
├── outputs/              # Generated plots and CSVs
├── run_simulation.py     # Main entry point
└── requirements.txt      # Mesa 2.x, pandas, matplotlib, numpy
```

## Running the Model

```bash
# Install dependencies
pip install -r requirements.txt

# Run single mineral
python run_simulation.py --mineral lithium --steps 200

# Run all minerals
python run_simulation.py --all --steps 200

# Custom parameters
python run_simulation.py --mineral nickel --steps 300 --geo-prob 0.02
```

## Key Outputs

1. **Plots**: `outputs/{mineral}_supply_chain_analysis.png`
2. **Data**: `outputs/{mineral}_model_data.csv`
3. **Summary**: `outputs/{mineral}_summary_stats.txt`

## Validation Checks

- ✓ Total production matches USGS data (±10%)
- ✓ Price stabilizes without shocks
- ✓ Recycling reaches 10-20% of supply by step 100
- ✓ Geopolitical events cause 20-50% price spikes
- ✓ Substitution reduces intensity by 20-30% under sustained high prices

## Next Steps After Approval

1. Implement data loader
2. Build agent classes (one by one)
3. Assemble main model
4. Create visualizations
5. Run and validate scenarios

---
**For detailed technical specifications, see [`architecture_plan.md`](architecture_plan.md)**
