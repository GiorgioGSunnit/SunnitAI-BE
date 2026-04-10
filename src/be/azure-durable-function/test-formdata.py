import requests
import sys
from requests_toolbelt.multipart.encoder import MultipartEncoder
import json

# Get filenames from command line or use defaults
file1_name = sys.argv[1] if len(sys.argv) > 1 else "testdata/NEW_1to5.pdf"
file2_name = sys.argv[2] if len(sys.argv) > 2 else "testdata/OLD_1to5.pdf"

# URL for the endpoint
url = "http://localhost:7071/api/compare_requirements"

print(f"Comparing files: {file1_name} and {file2_name}")

# Create proper multipart/form-data
mp_encoder = MultipartEncoder(
    fields={
        "file1Name": file1_name,
        "file2Name": file2_name,
        "file1IsExternal": "1",
        "file2IsExternal": "1",
    }
)

try:
    # Send the request with properly encoded multipart/form-data
    print("Sending multipart/form-data request with filenames...")
    print(f"Request headers: Content-Type: {mp_encoder.content_type}")
    print(f"Request data: {mp_encoder.fields}")

    headers = {"Content-Type": mp_encoder.content_type}
    response = requests.post(url, data=mp_encoder, headers=headers)

    # Print the response and request details
    print(f"\nRequest headers sent: {response.request.headers}")
    print(f"Status Code: {response.status_code}")

    # Try to parse the response
    try:
        resp_data = response.json()
        print(f"Response JSON: {json.dumps(resp_data, indent=2)}")
    except:
        print(f"Response text (not JSON): {response.text}")

    # If it's a 404, try with JSON data instead
    if response.status_code == 404:
        print("\nTrying with JSON data instead...")
        json_data = {
            "file1Name": file1_name,
            "file2Name": file2_name,
            "file1IsExternal": True,
            "file2IsExternal": True,
        }

        json_headers = {"Content-Type": "application/json"}
        json_response = requests.post(url, json=json_data, headers=json_headers)

        print(f"JSON Request headers sent: {json_response.request.headers}")
        print(f"Status Code with JSON: {json_response.status_code}")

        try:
            json_resp_data = json_response.json()
            print(f"JSON Response: {json.dumps(json_resp_data, indent=2)}")
        except:
            print(f"JSON Response text (not JSON): {json_response.text}")

except Exception as e:
    print(f"Error making request: {str(e)}")
