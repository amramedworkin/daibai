"""
DaiBai Knowledge Base Trainer.

Trains the agent on database schemas and domain knowledge.
"""

import asyncio
from pathlib import Path
from typing import Optional

from ..core.config import load_config, Config
from ..core.agent import DaiBaiAgent


def train_database(
    agent: DaiBaiAgent,
    db_name: Optional[str] = None,
    verbose: bool = False
) -> dict:
    """
    Train the agent on a database schema.
    
    Args:
        agent: DaiBaiAgent instance
        db_name: Database name to train on (uses default if not provided)
        verbose: Print progress
    
    Returns:
        Training statistics
    """
    stats = {"tables": 0, "columns": 0, "relationships": 0}
    
    if verbose:
        print(f"Training on database: {db_name or agent.current_database}")
    
    try:
        # Get schema
        schema = agent.get_schema(db_name, refresh=True)
        
        if verbose:
            print(f"Schema loaded: {len(schema)} characters")
        
        # Count tables
        stats["tables"] = schema.count("-- Table:")
        
        if verbose:
            print(f"Found {stats['tables']} tables")
        
    except Exception as e:
        if verbose:
            print(f"Error training: {e}")
    
    return stats


def main():
    """CLI entry point for training."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Train DaiBai on database schemas")
    parser.add_argument("-d", "--database", help="Database to train on")
    parser.add_argument("-c", "--config", help="Path to daibai.yaml")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")
    
    args = parser.parse_args()
    
    config = None
    if args.config:
        config = load_config(Path(args.config))
    
    agent = DaiBaiAgent(config)
    
    if args.database:
        agent.switch_database(args.database)
    
    stats = train_database(agent, args.database, args.verbose)
    
    print(f"\nTraining complete:")
    print(f"  Tables: {stats['tables']}")


if __name__ == "__main__":
    main()
