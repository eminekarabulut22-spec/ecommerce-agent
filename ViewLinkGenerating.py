from google.oauth2 import service_account
from googleapiclient.discovery import build

SERVICE_ACCOUNT_FILE = 'C:/Users/22147153364/singular-link-automation-de59ae5bffdb.json'
SCOPES = ['https://www.googleapis.com/auth/drive.readonly']

credentials = service_account.Credentials.from_service_account_file(
    SERVICE_ACCOUNT_FILE, scopes=SCOPES)
service = build('drive', 'v3', credentials=credentials)

ROOT_FOLDER_ID = '1-OUMimhAINyKSH3_1pmXJSfeLX6MnNG9'

def get_all_links(folder_id):
    links = []
    folders_to_process = [folder_id]

    while folders_to_process:
        current_folder = folders_to_process.pop()

        results = service.files().list(
            q=f"'{current_folder}' in parents and trashed = false",
            fields="files(id, name, mimeType)",
        ).execute()

        items = results.get('files', [])

        for item in items:
            file_id = item['id']
            if item['mimeType'] == 'application/vnd.google-apps.folder':
                folders_to_process.append(file_id)
            else:
                # Sadece paylaşım linkini üret (herkese açık değilse bile)
                link = f"https://drive.google.com/file/d/{file_id}/view"
                links.append((item['name'], link))

    return links


all_links = get_all_links(ROOT_FOLDER_ID)


for name, url in all_links:
    print(f"{name}: {url}")