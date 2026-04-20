"""
Main entry point for the Arb Execution Engine
"""

import asyncio
import sys
from engine import ExecutionEngine
from dotenv import load_dotenv

load_dotenv()

async def main():
    """Main execution loop"""
    print("🚀 Arb Execution Engine")
    print("=" * 60)
    print("Mode: Scan and Execute")
    print("=" * 60)
    
    async with ExecutionEngine() as engine:
        # Run continuous execution
        await engine.run_continuous(
            scan_interval_seconds=10
        )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\nShutting down...")
        sys.exit(0)
