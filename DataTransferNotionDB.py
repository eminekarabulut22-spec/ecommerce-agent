from notion_client import Client
import os


notion = Client(auth="")

SOURCE_DB_ID = "11151e04fe638009beb9c0890e79e677"
TARGET_DB_ID = "1e50d165ffcf803abda4f8faf7499ef7"


def get_source_data():
    results = []
    next_cursor = None

    while True:
        response = notion.databases.query(
            **{
                "database_id": SOURCE_DB_ID,
                "start_cursor": next_cursor
            }
        )
        results.extend(response["results"])
        next_cursor = response.get("next_cursor")
        if not next_cursor:
            break
    return results


def transfer_data(pages):
    for page in pages:
        props = page["properties"]

        # Extract text from each property (adjust as needed)
        product_name = props["Product Name"]["title"][0]["text"]["content"] if props["Product Name"]["title"] else ""
        suggested_product_page = props["Suggested Product Page"]["url"] if props["Suggested Product Page"].get("url") else ""
        title_tag = props["Title Tag"]["rich_text"][0]["text"]["content"] if props["Title Tag"]["rich_text"] else ""
        meta_tag = props["Meta Tag"]["rich_text"][0]["text"]["content"] if props["Meta Tag"]["rich_text"] else ""
        category_url = props["Category URL"]["rich_text"][0]["text"]["content"] if props["Category URL"]["rich_text"] else ""
        gtin = props["GTIN"]["rich_text"][0]["text"]["content"] if props["GTIN"]["rich_text"] else ""
        suggestions = props["Suggestions"]["rich_text"][0]["text"]["content"] if props["Suggestions"]["rich_text"] else ""
        variance_suggested_products = props["Variance - Suggested Products"]["rich_text"][0]["text"]["content"] if props["Variance - Suggested Products"]["rich_text"] else ""
        tags = props["Tags"]["multi_select"] if props["Tags"].get("multi_select") else []
        amazon_price = props["Amazon Price"]["number"] if props["Amazon Price"].get("number") else None
        category_url_ai = props["Category URL AI"]["url"] if props["Category URL AI"].get("url") else ""
        dimensions = props["Dimensions"]["rich_text"][0]["text"]["content"] if props["Dimensions"]["rich_text"] else ""
        elmasi_price = props["Elmasi Price"]["number"] if props["Elmasi Price"].get("number") else None
        manufacturer = props["Manufacturer"]["rich_text"][0]["text"]["content"] if props["Manufacturer"]["rich_text"] else ""
        meta_tag_ai = props["Meta Tag AI"]["rich_text"][0]["text"]["content"] if props["Meta Tag AI"]["rich_text"] else ""
        meta_tag_arabic = props["Meta Tag Arabic"]["rich_text"][0]["text"]["content"] if props["Meta Tag Arabic"]["rich_text"] else ""
        min_price_saudi_uae = props["Minimum Price found in Saudi Arabia & UAE"]["number"] if props["Minimum Price found in Saudi Arabia & UAE"].get("number") else None
        noon_price = props["Noon Price"]["number"] if props["Noon Price"].get("number") else None
        product_tag_souqra = props["Product Tag (for Souqra)"]["rich_text"][0]["text"]["content"] if props["Product Tag (for Souqra)"]["rich_text"] else ""
        product_url = props["Product URL"]["url"] if props["Product URL"].get("url") else ""
        product_url_ai = props["Product URL AI"]["url"] if props["Product URL AI"].get("url") else ""
        title_tag_ai = props["Title Tag AI"]["rich_text"][0]["text"]["content"] if props["Title Tag AI"]["rich_text"] else ""
        title_tag_arabic = props["Title Tag Arabic"]["rich_text"][0]["text"]["content"] if props["Title Tag Arabic"]["rich_text"] else ""
        trendyol_price = props["Trendyol Price"]["number"] if props["Trendyol Price"].get("number") else None
        turkishsouq_price = props["Turkishsouq Price"]["number"] if props["Turkishsouq Price"].get("number") else None
        weight = props["Weight"]["number"] if props["Weight"].get("number") else None

      
        notion.pages.create(
            parent={"database_id": TARGET_DB_ID},
            properties={
                "Product Name": {
                    "title": [{"text": {"content": product_name}}]
                },
                "Suggested Product Page": {
                    "url": suggested_product_page
                },
                "Title Tag": {
                    "rich_text": [{"text": {"content": title_tag}}]
                },
                "Meta Tag": {
                    "rich_text": [{"text": {"content": meta_tag}}]
                },
                "Category URL": {
                    "rich_text": [{"text": {"content": category_url}}]
                },
                "GTIN": {
                    "rich_text": [{"text": {"content": gtin}}]
                },
                "Suggestions": {
                    "rich_text": [{"text": {"content": suggestions}}]
                },
                "Variance - Suggested Products": {
                    "rich_text": [{"text": {"content": variance_suggested_products}}]
                },
                "Tags": {
                    "multi_select": [{"name": tag} for tag in tags]
                },
                "Amazon Price": {
                    "number": amazon_price
                },
                "Category URL AI": {
                    "url": category_url_ai
                },
                "Dimensions": {
                    "rich_text": [{"text": {"content": dimensions}}]
                },
                "Elmasi Price": {
                    "number": elmasi_price
                },
                "Manufacturer": {
                    "rich_text": [{"text": {"content": manufacturer}}]
                },
                "Meta Tag AI": {
                    "rich_text": [{"text": {"content": meta_tag_ai}}]
                },
                "Meta Tag Arabic": {
                    "rich_text": [{"text": {"content": meta_tag_arabic}}]
                },
                "Minimum Price found in Saudi Arabia & UAE": {
                    "number": min_price_saudi_uae
                },
                "Noon Price": {
                    "number": noon_price
                },
                "Product Tag (for Souqra)": {
                    "rich_text": [{"text": {"content": product_tag_souqra}}]
                },
                "Product URL": {
                    "url": product_url
                },
                "Product URL AI": {
                    "url": product_url_ai
                },
                "Title Tag AI": {
                    "rich_text": [{"text": {"content": title_tag_ai}}]
                },
                "Title Tag Arabic": {
                    "rich_text": [{"text": {"content": title_tag_arabic}}]
                },
                "Trendyol Price": {
                    "number": trendyol_price
                },
                "Turkishsouq Price": {
                    "number": turkishsouq_price
                },
                "Weight": {
                    "number": weight
                }
            }
        )

pages = get_source_data()
transfer_data(pages)

print("Data transfer complete!")