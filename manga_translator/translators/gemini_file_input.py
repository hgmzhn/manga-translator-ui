"""Map shared translation attachments to Gemini native inline file parts."""


def to_gemini_parts(content):
    if isinstance(content, str):
        return [{"text": content}]
    parts = []
    for item in content:
        if item["type"] == "text":
            parts.append({"text": item["text"]})
        elif item["type"] == "file":
            header, data = item["file"]["file_data"].split(",", 1)
            if header != "data:text/plain;base64":
                raise ValueError("Gemini translation expects a UTF-8 text/plain attachment")
            parts.append({"inlineData": {"mimeType": "text/plain", "data": data}})
        else:
            raise ValueError("Unsupported Gemini translation input: " + item["type"])
    return parts
