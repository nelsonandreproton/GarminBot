import garminconnect, json, os
from pathlib import Path

email = input("Garmin email: ")
password = input("Garmin password: ")

client = garminconnect.Garmin(email, password)
client.login()
token = client.garth.dumps()

Path("./data").mkdir(exist_ok=True)
Path("./data/garmin_tokens.json").write_text(token, encoding="utf-8")
print("Token saved to ./data/garmin_tokens.json")