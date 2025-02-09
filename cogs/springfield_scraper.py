import requests
from bs4 import BeautifulSoup
import time

# Base URL pattern
base_url = "https://www.springfieldspringfield.co.uk/view_episode_scripts.php?tv-show=deadwood&episode=s{:02d}e{:02d}"

# Headers to mimic a real browser request
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"
}

# Number of seasons and episodes per season
num_seasons = 3
episodes_per_season = 12

# Dictionary to store transcripts
transcripts = {}

# Loop through each season and episode
for season in range(1, num_seasons + 1):
    for episode in range(1, episodes_per_season + 1):
        # Format the URL
        episode_url = base_url.format(season, episode)
        print(f"Fetching: {episode_url}")

        # Send GET request
        response = requests.get(episode_url, headers=headers)

        # Check if the request was successful
        if response.status_code == 200:
            # Parse the HTML content
            soup = BeautifulSoup(response.text, "html.parser")

            # Find the div with class 'scrolling-script-container'
            script_div = soup.find("div", class_="scrolling-script-container")

            if script_div:
                # Extract text content
                script_text = script_div.get_text(separator="\n", strip=True)
                transcripts[f"S{season:02d}E{episode:02d}"] = script_text
                print(f"✅ Successfully scraped S{season:02d}E{episode:02d}")
            else:
                print(f"❌ No script found for S{season:02d}E{episode:02d}")
        else:
            print(f"❌ Failed to retrieve S{season:02d}E{episode:02d} (Status Code: {response.status_code})")

        # Pause to avoid triggering anti-scraping measures
        time.sleep(1)

# Save transcripts to a text file
with open("deadwood_transcripts.txt", "w", encoding="utf-8") as file:
    for episode, script in transcripts.items():
        file.write(f"--- {episode} ---\n{script}\n\n")

print("✅ All transcripts saved to 'deadwood_transcripts.txt'")
