import asyncio
import time
import uuid
import sys
import logging
from tabulate import tabulate

from voxos.core.task_manager.manager import get_task_manager
from voxos.agents.listener.agent import ListenerAgent
from voxos.agents.strategist.agent import StrategistAgent
from voxos.agents.executor.agent import ExecutorAgent
from voxos.agents.overseer.agent import OverseerAgent
from voxos.config import get_settings

# Suppress debug logs for clean output
logging.getLogger("voxos").setLevel(logging.WARNING)

BENCHMARK_TASKS = [
    # System Info
    "What operating system am I currently running?",
    "What is the exact time right now?",
    "How much RAM is my computer using right now?",
    
    # Filesystem (Deterministic / Fast Path)
    "List all the files on my Desktop.",
    "Create a new folder on my Desktop called 'VoxBenchmark'",
    "Inside the VoxBenchmark folder, create a file named 'hello.txt' that says 'Benchmark success'.",
    "Read the contents of Desktop/VoxBenchmark/hello.txt",
    "Rename Desktop/VoxBenchmark/hello.txt to Desktop/VoxBenchmark/success.txt",
    "Copy Desktop/VoxBenchmark/success.txt to Desktop/VoxBenchmark/copy.txt",
    "Delete the VoxBenchmark folder on my Desktop. Make sure to delete recursively.",
    
    # Application Control
    "Open Notepad.",
    "What applications are currently running on my system?",
    "Close the Notepad application.",
    
    # Terminal / Shell
    "Run a shell command to echo 'Hello World'.",
    
    # Browser
    "Open my default browser and navigate to github.com.",
    
    # Failure Recovery / Security
    "Delete the C:\\Windows folder.", # Should be blocked
    "Close an application named 'FakeAppThatDoesntExist'." # Should fail gracefully
]

async def run_benchmark():
    print(f"Starting VoxOS Benchmark Suite ({len(BENCHMARK_TASKS)} tasks)...\n")
    
    manager = get_task_manager()
    listener = ListenerAgent()
    strategist = StrategistAgent()
    executor = ExecutorAgent()
    overseer = OverseerAgent()
    
    # Run tasks sequentially
    session_id = str(uuid.uuid4())
    
    results = []
    
    for i, user_request in enumerate(BENCHMARK_TASKS, 1):
        print(f"[{i}/{len(BENCHMARK_TASKS)}] Request: '{user_request}'")
        
        # We need to bypass create_task missing method. In manager.py it is submit_task maybe or we just create it.
        # Wait, the method in TaskManager is submit_task?
        # Let's check manager.py for task creation.
        # It's manager.submit_task(user_request, session_id) or create_task?
        # I'll import Task and add it to manager manually or use submit_task.
        task = await manager.submit_task(user_request, session_id)
        
        t0 = time.time()
        try:
            completed_task = await manager.run_pipeline(
                task=task,
                listener=listener,
                strategist=strategist,
                executor=executor,
                overseer=overseer
            )
            
            t1 = time.time()
            total_time = round(t1 - t0, 2)
            
            status = completed_task.status.value
            
            # Formatting output
            routing = getattr(completed_task, 'execution_class', 'UNKNOWN')
            if routing == 'UNKNOWN':
                # check if listener put it in plan
                routing = 'LLM'
            
            results.append([
                i, 
                user_request[:40] + "...", 
                status, 
                f"{total_time}s", 
                completed_task.error_info.get("reason", "") if completed_task.error_info else ""
            ])
            
            if status == "COMPLETED":
                print(f"  -> SUCCESS in {total_time}s")
            else:
                print(f"  -> {status} in {total_time}s")
                if completed_task.error_info:
                    print(f"     Reason: {completed_task.error_info.get('reason')}")
                    
        except Exception as e:
            print(f"  -> CRASHED: {e}")
            results.append([i, user_request[:40] + "...", "CRASHED", "-", str(e)])
            
    print("\n\n" + "="*60)
    print("BENCHMARK RESULTS")
    print("="*60)
    print(tabulate(results, headers=["#", "Request", "Status", "Time", "Error Info"], tablefmt="grid"))
    
    success_count = sum(1 for r in results if r[2] == "COMPLETED")
    expected_failures = sum(1 for r in results if r[2] == "FAILED") # some tasks are designed to fail
    print(f"\nCompleted Tasks: {success_count} / {len(BENCHMARK_TASKS)}")

if __name__ == "__main__":
    asyncio.run(run_benchmark())
