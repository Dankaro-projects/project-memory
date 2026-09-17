"""Project Memory regression tests."""
import os

# No test reads the transcripts of the user who runs the suite. Session tests switch reading on with their own folders.
os.environ['PROJECT_MEMORY_SESSIONS'] = 'off'
