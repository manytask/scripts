# curl --fail -X POST -H "Authorization: Bearer $TESTER_TOKEN" "https://app.manytask.org/api/$COURSE_NAME/report" -H "Content-Type: application/x-www-form-urlencoded"  -d "task=vm&username=zhmurov&score=322&check_deadline=False"

import pandas as pd
import os
import requests
import math
import argparse

# Parse command-line arguments
parser = argparse.ArgumentParser(
    description="Upload scores from CSV to Manytask API",
    formatter_class=argparse.RawDescriptionHelpFormatter,
    epilog="""
Example usage:
  python set_scores.py -t YOUR_TOKEN -f scores.csv -c python_2025_fall
    """
)
parser.add_argument(
    "-t", "--token",
    required=True,
    help="Tester token for API authentication"
)
parser.add_argument(
    "-f", "--file",
    required=True,
    help="Path to the CSV file containing scores"
)
parser.add_argument(
    "-c", "--course",
    required=True,
    help="Course name (e.g., python_2025_fall)"
)

args = parser.parse_args()

# Use the parsed arguments
csv_file_path = args.file
course_name = args.course
tester_token = args.token

# Define your API endpoint and headers
api_url = f"https://app.manytask.org/api/{course_name}/report"
headers = {"Authorization": "Bearer " + str(tester_token), "Content-Type": "application/x-www-form-urlencoded"}

# Read the CSV file into a DataFrame
df = pd.read_csv(csv_file_path)

# Iterate over each row in the DataFrame
for index, row in df.iterrows():
    username = row["username"]  # Adjust the column name if necessary
    try:
        score = math.ceil(row["scores.vm"])  # Adjust the column name if necessary
    except:
        score = 0

    # Define the data to be sent in the POST request
    data = {"task": "vm", "username": username, "score": score, "check_deadline": "False"}

    try:
        print(api_url)
        print(headers)

        # Send the POST request
        response = requests.post(api_url, headers=headers, data=data)

        print(response)

        # Check if the request was successful
        if response.status_code == 200:
            print(f"Successfully sent data for username: {username}, score: {score}")
        else:
            print(f"Failed to send data for username: {username}, score: {score}. Status code: {response.status_code}")
            print(response.text)  # Print the response text for debugging

    except requests.exceptions.RequestException as e:
        print(f"An error occurred while sending data for username: {username}, score: {score}. Error: {e}")
