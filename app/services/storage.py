from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.core.config import get_settings


settings = get_settings()


@dataclass
class StoredObject:
    storage_path: str
    size_bytes: int
    provider: str


class StorageService(Protocol):
    def upload_bytes(self, *, content: bytes, destination_path: str, content_type: str) -> StoredObject: ...

    def open_bytes(self, storage_path: str) -> bytes: ...

    def exists(self, storage_path: str) -> bool: ...

    def create_presigned_download_url(self, storage_path: str, expires_in: int) -> str | None: ...


class LocalStorageService:
    def __init__(self, base_path: Path) -> None:
        self.base_path = base_path
        self.base_path.mkdir(parents=True, exist_ok=True)

    def upload_bytes(self, *, content: bytes, destination_path: str, content_type: str) -> StoredObject:
        target = self.base_path / destination_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        return StoredObject(storage_path=destination_path, size_bytes=len(content), provider="local")

    def open_bytes(self, storage_path: str) -> bytes:
        return (self.base_path / storage_path).read_bytes()

    def exists(self, storage_path: str) -> bool:
        return (self.base_path / storage_path).is_file()

    def create_presigned_download_url(self, storage_path: str, expires_in: int) -> str | None:
        return None


class S3StorageService:
    def __init__(self, client, bucket_name: str) -> None:
        self.client = client
        self.bucket_name = bucket_name

    def upload_bytes(self, *, content: bytes, destination_path: str, content_type: str) -> StoredObject:
        stream = io.BytesIO(content)
        self.client.upload_fileobj(
            stream,
            self.bucket_name,
            destination_path,
            ExtraArgs={"ContentType": content_type},
        )
        return StoredObject(storage_path=destination_path, size_bytes=len(content), provider="s3")

    def open_bytes(self, storage_path: str) -> bytes:
        stream = io.BytesIO()
        self.client.download_fileobj(self.bucket_name, storage_path, stream)
        return stream.getvalue()

    def exists(self, storage_path: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket_name, Key=storage_path)
        except Exception:
            return False
        return True

    def create_presigned_download_url(self, storage_path: str, expires_in: int) -> str | None:
        return self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket_name, "Key": storage_path},
            ExpiresIn=expires_in,
        )


def build_storage_service() -> StorageService:
    if settings.storage_backend == "s3":
        import boto3

        client = boto3.client(
            "s3",
            region_name=settings.storage_region,
            endpoint_url=settings.storage_endpoint_url,
            aws_access_key_id=settings.storage_access_key_id,
            aws_secret_access_key=settings.storage_secret_access_key,
        )
        return S3StorageService(client=client, bucket_name=settings.storage_bucket_name)
    return LocalStorageService(base_path=settings.local_storage_path)
