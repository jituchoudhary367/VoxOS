"""
CLI entry point to test the VoxOS voice pipeline locally.
"""

import asyncio
import logging
import os
from voxos.voice.pipeline import VoicePipeline

# Configure logging for the test
logging.basicConfig(level=logging.INFO)

async def main():
    print("Starting VoxOS Voice Pipeline...")
    # Provide a dummy session ID for testing
    pipeline = VoicePipeline(session_id="local_test_session")
    try:
        await pipeline.start()
    except KeyboardInterrupt:
        print("Stopping VoxOS Voice Pipeline...")
        await pipeline.stop()
    except Exception as e:
        print(f"Error: {e}")
        await pipeline.stop()

if __name__ == "__main__":
    # Usually GROQ_API_KEY is needed.
    if not os.getenv("GROQ_API_KEY") and not os.getenv("OPENAI_API_KEY"):
        print("WARNING: GROQ_API_KEY or OPENAI_API_KEY environment variable is not set. Pipecat services might fail.")
    
    asyncio.run(main())
