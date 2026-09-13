def convert_to_direct_link(drive_links):
    direct_links = []
    for link in drive_links:
        match = re.search(r"\/file\/d\/([a-zA-Z0-9_-]+)", link)
        if match:
            file_id = match.group(1)
            direct_link = f"https://drive.google.com/uc?export=view&id={file_id}"
            direct_links.append(direct_link)
        else:
            direct_links.append("Geçersiz link formatı")
    return direct_links


drive_file_links = [
    "https://drive.google.com/file/d/1g73oKNO-RWfV2QRfDt5Mac3WnKq82MYi/view",
    "https://drive.google.com/file/d/1QulcMQjCBeQSwt9htZyLPv8KJa4Bdk9F/view?usp=sharing",
    "https://drive.google.com/file/d/1fUEpatiiVueJUO_1vmtfDgyTaYai0T9l/view?usp=sharing"
]

direct_links = convert_to_direct_link(drive_file_links)

for original, converted in zip(drive_file_links, direct_links):
    print(f"Orijinal: {original}")
    print(f"Direct : {converted}\n")