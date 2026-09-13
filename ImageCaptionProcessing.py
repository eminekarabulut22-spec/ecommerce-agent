import openai
import os
import google.auth
import requests
import base64
from googleapiclient.discovery import build
from google.oauth2 import service_account


openai.api_key = ""

SPREADSHEET_ID = "1VSdyXfzMfF5bhP-JBZO7CQ_2Y3JtvCoLL3m4NYyN9h8"
RANGE = "Sheet1!D2:D"

def authenticate_google_sheets():
    creds = service_account.Credentials.from_service_account_file(
        "singular-link-automation-de59ae5bffdb.json", scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    service = build("sheets", "v4", credentials=creds)
    return service

def download_and_encode_image(url):
    try:
        response = requests.get(url)
        if response.status_code == 200:
            return base64.b64encode(response.content).decode("utf-8")
        else:
            return None
    except Exception as e:
        return None

def generate_caption(base64_image):
    try:
        response = openai.chat.completions.create(
            model="gpt-4o",  
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Bu görseli detaylı şekilde açıkla."},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{base64_image}"
                            },
                        },
                    ],
                }
            ],
            max_tokens=300
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"Hata: {e}"

def write_to_sheet(service, captions):
    service.spreadsheets().values().update(
        spreadsheetId=SPREADSHEET_ID,
        range='Sheet1!D1',
        valueInputOption="RAW",
        body={"values": [["Captions"]]}
    ).execute()

    values = [[caption] for caption in captions]
    body = {
        "values": values
    }
    result = service.spreadsheets().values().update(
        spreadsheetId=SPREADSHEET_ID,
        range=RANGE,
        valueInputOption="RAW",
        body=body
    ).execute()
    print(f"{result.get('updatedCells')} cells updated.")

def main():
    file_path = "C:/Users/22147153364/Documents/Souqra_Notes/image_urls.txt"
    with open(file_path, "r", encoding="utf-8") as file:
        urls = [line.strip() for line in file if line.strip()]

   # urls = urls[:2]

    service = authenticate_google_sheets()

    captions = []
    for i, url in enumerate(urls, start=1):
        print(f"{i}. görsel işleniyor...")
        base64_img = download_and_encode_image(url)
        if base64_img:
            caption = generate_caption(base64_img)
        else:
            caption = "Görsel indirilemedi."
        captions.append(caption)

    write_to_sheet(service, captions)

if __name__ == "__main__":
    main()