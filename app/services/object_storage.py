def delete_all_versions(client, bucket: str, key: str) -> None:
    key_marker = None
    version_marker = None
    objects = []
    while True:
        arguments = {"Bucket": bucket, "Prefix": key}
        if key_marker:
            arguments["KeyMarker"] = key_marker
        if version_marker:
            arguments["VersionIdMarker"] = version_marker
        page = client.list_object_versions(**arguments)
        objects.extend(
            {"Key": item["Key"], "VersionId": item["VersionId"]}
            for group in ("Versions", "DeleteMarkers")
            for item in page.get(group, [])
            if item["Key"] == key
        )
        if not page.get("IsTruncated"):
            break
        key_marker = page["NextKeyMarker"]
        version_marker = page["NextVersionIdMarker"]
    if not objects:
        client.delete_object(Bucket=bucket, Key=key)
        return
    for offset in range(0, len(objects), 1000):
        client.delete_objects(
            Bucket=bucket,
            Delete={"Objects": objects[offset : offset + 1000], "Quiet": True},
        )
