"""
Data loader for USGS Critical Minerals Mapping data.
Parses USGS_CMM.csv and extracts production, reserves, and demand data.
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Tuple


class USGSDataLoader:
    """Loads and processes USGS Critical Minerals Mapping data."""
    
    def __init__(self, csv_path: str = "USGS_CMM.csv"):
        """Initialize the data loader.
        
        Args:
            csv_path: Path to USGS_CMM.csv file
        """
        self.csv_path = csv_path
        self.data = None
        self._load_data()
    
    def _load_data(self):
        """Load the CSV file into memory."""
        try:
            self.data = pd.read_csv(self.csv_path)
            print(f"Loaded USGS data: {len(self.data)} countries")
        except Exception as e:
            raise FileNotFoundError(f"Could not load {self.csv_path}: {e}")
    
    def get_producing_countries(self, mineral: str) -> pd.DataFrame:
        """Get countries that produce a specific mineral.
        
        Args:
            mineral: Mineral name (e.g., 'Lithium', 'Nickel', 'Platinum')
        
        Returns:
            DataFrame with producing countries and their data
        """
        production_col = f"{mineral}_Production_2025"
        reserves_col = f"{mineral}_Reserves"
        
        if production_col not in self.data.columns:
            raise ValueError(f"Mineral '{mineral}' not found in dataset")
        
        # Filter countries with production > 0
        producers = self.data[self.data[production_col] > 0].copy()
        
        # Select relevant columns
        cols = ['Country', production_col, reserves_col]
        
        # Add demand columns if available
        demand_cols = [col for col in self.data.columns if mineral in col and 'Demand' in col]
        cols.extend(demand_cols)
        
        # Add deposit count if available
        deposits_col = f"{mineral}_Num_Deposits"
        if deposits_col in self.data.columns:
            cols.append(deposits_col)
        
        result = producers[cols].copy()
        result = result.fillna(0)  # Replace NaN with 0
        
        print(f"{mineral}: Found {len(result)} producing countries")
        return result
    
    def get_global_demand(self, mineral: str) -> Dict[str, float]:
        """Get global demand forecasts for a mineral.
        
        Args:
            mineral: Mineral name
        
        Returns:
            Dictionary with demand values for different years
        """
        demand_data = {}
        demand_cols = [col for col in self.data.columns if mineral in col and 'Demand' in col]
        
        for col in demand_cols:
            # Extract year from column name (e.g., "Lithium_Global_Demand_2024")
            parts = col.split('_')
            year_idx = [i for i, p in enumerate(parts) if p.isdigit()]
            if year_idx:
                year = parts[year_idx[0]]
                scenario = '_'.join(parts[year_idx[0]+1:]) if len(parts) > year_idx[0]+1 else 'baseline'
                key = f"{year}_{scenario}" if scenario else year
                
                # Take the first non-zero value from the data
                values = self.data[col].dropna()
                demand_data[key] = values.iloc[0] if len(values) > 0 else 0
        
        return demand_data
    
    def calculate_market_shares(self, mineral: str) -> pd.DataFrame:
        """Calculate market share percentage for each producing country.
        
        Args:
            mineral: Mineral name
        
        Returns:
            DataFrame with market share percentages
        """
        producers = self.get_producing_countries(mineral)
        production_col = f"{mineral}_Production_2025"
        
        total_production = producers[production_col].sum()
        producers['Market_Share_Pct'] = (producers[production_col] / total_production) * 100
        
        return producers
    
    def derive_mine_parameters(self, country: str, mineral: str, 
                               base_extraction_cost: float = 10000) -> Dict:
        """Derive mine parameters for a specific country and mineral.
        
        Args:
            country: Country name
            mineral: Mineral name
            base_extraction_cost: Base extraction cost in $/ton
        
        Returns:
            Dictionary with mine parameters
        """
        producers = self.get_producing_countries(mineral)
        country_data = producers[producers['Country'] == country]
        
        if len(country_data) == 0:
            raise ValueError(f"{country} does not produce {mineral}")
        
        country_data = country_data.iloc[0]
        production_col = f"{mineral}_Production_2025"
        reserves_col = f"{mineral}_Reserves"
        
        production = country_data[production_col]
        reserves = country_data[reserves_col]
        
        # Derive ore grade from reserves/production ratio
        # Higher reserves relative to production suggests lower grade (more rock to process)
        if reserves > 0 and production > 0:
            reserve_production_ratio = reserves / production
            # Normalize: typical ratio 50-500, map to ore_grade 0.4-0.95
            ore_grade = np.clip(1.0 - (reserve_production_ratio / 1000), 0.4, 0.95)
        else:
            ore_grade = 0.7  # Default
        
        # Regional cost multipliers (simplified)
        regional_multipliers = {
            'China': 0.8, 'Indonesia': 0.9, 'Russia': 1.1, 'Australia': 1.3,
            'Canada': 1.2, 'United States': 1.4, 'Chile': 1.0, 'Brazil': 0.95,
            'South Africa': 1.0, 'Congo (Kinshasa)': 0.7, 'Philippines': 0.85,
            'Other countries': 1.0
        }
        
        multiplier = regional_multipliers.get(country, 1.0)
        extraction_cost = base_extraction_cost * multiplier
        
        return {
            'jurisdiction': country,
            'production_capacity': production,  # tons/step
            'reserves': reserves,
            'ore_grade': ore_grade,
            'extraction_cost': extraction_cost
        }
    
    def get_all_mines_data(self, mineral: str, base_extraction_cost: float = 10000) -> List[Dict]:
        """Get mine parameters for all producing countries.
        
        Args:
            mineral: Mineral name
            base_extraction_cost: Base extraction cost
        
        Returns:
            List of dictionaries with mine parameters
        """
        producers = self.get_producing_countries(mineral)
        mines_data = []
        
        for _, row in producers.iterrows():
            country = row['Country']
            try:
                mine_params = self.derive_mine_parameters(country, mineral, base_extraction_cost)
                mines_data.append(mine_params)
            except Exception as e:
                print(f"Warning: Could not derive parameters for {country}: {e}")
        
        return mines_data


# Convenience function
def load_mineral_data(mineral: str, csv_path: str = "USGS_CMM.csv") -> Tuple[List[Dict], Dict]:
    """Load complete data for a mineral.
    
    Args:
        mineral: Mineral name
        csv_path: Path to USGS CSV
    
    Returns:
        Tuple of (mines_data, demand_data)
    """
    loader = USGSDataLoader(csv_path)
    mines_data = loader.get_all_mines_data(mineral)
    demand_data = loader.get_global_demand(mineral)
    
    return mines_data, demand_data
