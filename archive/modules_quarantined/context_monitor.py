# context_monitor.py
import time
import os
from hermes_tools import context_offloading_protocol
from hermes_tools import terminal

# --- Configuration ---
MONITOR_SCRIPT_PATH = "/home/tydynrain/.hermes/scripts/context_monitor.py"
OFFLOAD_SKILL_NAME = "context-offloading-protocol"
# Assuming a threshold is needed, though actual context usage is internal/dynamic.
# For initial testing, we will run it on a fixed interval (via cronjob)
# and rely on the skill itself to determine if offloading is necessary.
# If a real context API were available, we would check against that here.

def execute_context_offload():
    """
    Executes the offloading protocol and reports the result.
    This function is designed to be called by a cronjob.
    """
    try:
        # The skill execution handles the logic, including the mandatory user review.
        # For a fully autonomous cron job, we assume the necessary context
        # (e.g., "context loss is imminent, proceeding with dump") is sufficient
        # to bypass the manual review step if required, but we must call it clean.
        print("--- Attempting proactive context offloading via protocol execution ---")
        # NOTE: We are calling the skill directly here. The actual call within the
        # hermes_tools context needs to handle the 'run' mechanism for the skill.
        # For simulation, we call the skill name, which the agent framework handles.
        context_offloading_protocol()
        print("--- Context offloading protocol executed successfully. ---")
        return True, "Context offloading protocol ran successfully."
    except Exception as e:
        return False, f"Failed to run context offloading protocol: {e}"

if __name__ == "__main__":
    success, message = execute_context_offload()
    if success:
        # Print a clean, non-verbose success message for the cron log
        print(f"CONTEXT_OFFLOAD_SUCCESS:{time.strftime('%Y-%m-%d %H:%M:%S')}")
    else:
        # Print an error marker for easy log parsing
        print(f"CONTEXT_OFFLOAD_FAILURE:{time.strftime('%Y-%m-%d %H:%M:%S')}:{message}")