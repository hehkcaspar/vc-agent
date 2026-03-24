import requests
import json
import sys
from pathlib import Path

def test_preview():
    # Try common ports
    ports = [8000, 8001, 8002]
    resume_id = "6ffe87ca8abb" # From list_dir earlier
    
    for port in ports:
        url = f"http://localhost:{port}/api/preview/{resume_id}"
        print(f"Testing {url}...")
        try:
            response = requests.get(url, timeout=2)
            if response.status_code == 200:
                print(f"SUCCESS: Preview endpoint working on port {port}")
                print(f"Content-Type: {response.headers.get('Content-Type')}")
                return True
            else:
                print(f"FAILED: {response.status_code} - {response.text}")
        except Exception as e:
            print(f"Error on port {port}: {e}")
            
    return False

if __name__ == "__main__":
    if not test_preview():
        sys.exit(1)
