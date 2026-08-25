from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, File, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi import Query
from pydantic import BaseModel, Field

from .service import (
    MAX_REFERENCE_BYTES,
    HeadlessService,
    ServiceConfigurationError,
    service_from_environment,
)
from .app_updates import AndroidReleaseCatalog, AppReleaseError


class GenerationRequest(BaseModel):
    keyId: str = Field(min_length=1, max_length=120)
    prompt: str = Field(min_length=1, max_length=32000)
    referenceIds: list[str] = Field(default_factory=list, max_length=16)
    options: dict = Field(default_factory=dict)


class KeyRequest(BaseModel):
    keyId: str | None = Field(default=None, min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=120)
    value: str = Field(min_length=1, max_length=4096)
    baseUrl: str | None = Field(default=None, max_length=2048)


class PromptPolishRequest(BaseModel):
    keyId: str = Field(min_length=1, max_length=120)
    prompt: str = Field(min_length=1, max_length=32000)


class PairingRequest(BaseModel):
    pairingCode: str = Field(min_length=16, max_length=128)


class DeviceRegistrationRequest(BaseModel):
    deviceId: str = Field(min_length=16, max_length=128)
    keyId: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=120)
    value: str = Field(min_length=1, max_length=4096)
    baseUrl: str | None = Field(default=None, max_length=2048)


class ServiceApp:
    def __init__(
        self,
        service: HeadlessService,
        update_catalog: AndroidReleaseCatalog | None = None,
    ) -> None:
        self.service = service
        self.update_catalog = update_catalog
        self.app = FastAPI(title="API_TOOLS Service", version="1")
        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=["capacitor://localhost", "http://localhost", "https://localhost"],
            allow_credentials=False,
            allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
            allow_headers=["Accept", "Authorization", "Content-Type"],
            expose_headers=["X-Event-Epoch"],
        )
        self._register_routes()

    def owner(self, authorization: str | None) -> str:
        expected = f"Bearer {self.service.config.bearer_token}"
        if authorization == expected:
            return "paired-device"
        prefix = "Bearer "
        token = authorization[len(prefix):] if authorization and authorization.startswith(prefix) else ""
        owner = self.service.device_owner(token)
        if owner is None:
            raise HTTPException(status_code=401, detail="未授权")
        return owner

    def _register_routes(self) -> None:
        app = self.app

        @app.get("/healthz")
        def health() -> dict:
            return self.service.health()

        @app.get("/readyz")
        def ready() -> dict:
            result = self.service.readiness()
            if not result["ok"]:
                raise HTTPException(status_code=503, detail=result)
            return result

        @app.post("/api/v1/pair")
        def pair_device(request: PairingRequest) -> dict:
            try:
                return self.service.pair_device(request.pairingCode)
            except PermissionError as exc:
                raise HTTPException(status_code=401, detail=str(exc)) from exc

        @app.post("/api/v1/device/register")
        def register_device(request: DeviceRegistrationRequest) -> dict:
            try:
                return self.service.register_device(
                    request.deviceId,
                    request.keyId,
                    request.name,
                    request.value,
                    request.baseUrl,
                )
            except PermissionError as exc:
                raise HTTPException(status_code=401, detail=str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

        @app.get("/api/v1/app/update")
        def check_app_update(
            platform: str = Query(default="android"),
            currentVersionCode: int = Query(default=0, ge=0),
            currentBuildTimestampMs: int = Query(default=0, ge=0),
        ) -> dict:
            if platform != "android" or self.update_catalog is None:
                raise HTTPException(status_code=404, detail="没有可用的 Android 发布通道")
            try:
                return self.update_catalog.check(
                    current_version_code=currentVersionCode,
                    current_build_timestamp_ms=currentBuildTimestampMs,
                )
            except AppReleaseError as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc

        @app.get("/api/v1/app/update/download")
        def download_app_update(platform: str = Query(default="android")) -> FileResponse:
            if platform != "android" or self.update_catalog is None:
                raise HTTPException(status_code=404, detail="没有可用的 Android 发布通道")
            try:
                path, content_type = self.update_catalog.download()
            except AppReleaseError as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            return FileResponse(
                path,
                media_type=content_type,
                filename=path.name,
                headers={"Cache-Control": "no-cache"},
            )

        @app.get("/api/v1/state")
        def state(authorization: str | None = Header(default=None)) -> dict:
            owner = self.owner(authorization)
            return self.service.state(owner)

        @app.post("/api/v1/refresh")
        def refresh(authorization: str | None = Header(default=None)) -> dict:
            owner = self.owner(authorization)
            return self.service.refresh_quota(owner)

        @app.post("/api/v1/keys")
        def add_key(
            request: KeyRequest,
            authorization: str | None = Header(default=None),
        ) -> dict:
            owner = self.owner(authorization)
            return self.service.add_key(
                owner,
                request.name,
                request.value,
                request.baseUrl,
                request.keyId,
            )

        @app.delete("/api/v1/keys/{key_id}")
        def delete_key(
            key_id: str,
            authorization: str | None = Header(default=None),
        ) -> dict:
            owner = self.owner(authorization)
            return self.service.delete_key(owner, key_id)

        @app.post("/api/v1/prompts/polish")
        def polish_prompt(
            request: PromptPolishRequest,
            authorization: str | None = Header(default=None),
        ) -> dict:
            owner = self.owner(authorization)
            try:
                return self.service.polish_prompt(owner, request.keyId, request.prompt)
            except KeyError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc

        @app.post("/api/v1/uploads/references")
        async def upload(
            file: UploadFile = File(...),
            authorization: str | None = Header(default=None),
        ) -> dict:
            owner = self.owner(authorization)
            body = await file.read(MAX_REFERENCE_BYTES + 1)
            try:
                return {
                    "ok": True,
                    "resource": self.service.upload_reference(
                        owner,
                        file.filename or "reference",
                        file.content_type or "",
                        body,
                    ),
                }
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

        @app.get("/api/v1/image-sessions")
        def image_sessions(authorization: str | None = Header(default=None)) -> dict:
            owner = self.owner(authorization)
            return self.service.list_image_sets(owner)

        @app.post("/api/v1/image-generations")
        def create_generation(
            request: GenerationRequest,
            authorization: str | None = Header(default=None),
        ) -> dict:
            owner = self.owner(authorization)
            try:
                return self.service.create_generation(
                    owner,
                    request.keyId,
                    request.prompt,
                    request.referenceIds,
                    request.options,
                )
            except KeyError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

        @app.post("/api/v1/image-generations/{request_id}/cancel")
        def cancel(
            request_id: str,
            authorization: str | None = Header(default=None),
        ) -> dict:
            owner = self.owner(authorization)
            return self.service.cancel_task(owner, request_id)

        @app.get("/api/v1/image-generations/{request_id}")
        def task_state(
            request_id: str,
            authorization: str | None = Header(default=None),
        ) -> dict:
            owner = self.owner(authorization)
            try:
                return {"ok": True, "event": self.service.task_state(owner, request_id)}
            except KeyError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc

        @app.get("/api/v1/assets/{asset_id}")
        def asset(
            asset_id: str,
            authorization: str | None = Header(default=None),
        ) -> FileResponse:
            owner = self.owner(authorization)
            try:
                path, content_type = self.service.resolve_asset(owner, asset_id)
            except KeyError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            return FileResponse(path, media_type=content_type)

        @app.get("/api/v1/events")
        async def events(
            cursor: int = 0,
            authorization: str | None = Header(default=None),
        ) -> StreamingResponse:
            owner = self.owner(authorization)

            async def stream() -> AsyncIterator[str]:
                current = self.service.events.normalize_cursor(cursor)
                while True:
                    batch, current = await asyncio.to_thread(
                        self.service.events.wait_since,
                        owner,
                        current,
                        15.0,
                    )
                    if not batch:
                        yield ": keep-alive\n\n"
                        continue
                    for event in batch:
                        event_id = int(event["eventId"])
                        yield f"id: {event_id}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"

            return StreamingResponse(
                stream(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                    "X-Event-Epoch": self.service.events.epoch,
                },
            )


def create_app(
    service: HeadlessService | None = None,
    update_catalog: AndroidReleaseCatalog | None = None,
) -> FastAPI:
    if service is None:
        service = service_from_environment()
    if update_catalog is None:
        release_dir = os.environ.get("API_TOOLS_ANDROID_RELEASE_DIR", "").strip()
        if release_dir:
            update_catalog = AndroidReleaseCatalog(Path(release_dir))
    return ServiceApp(service, update_catalog).app


try:
    app = create_app()
except ServiceConfigurationError as configuration_error:
    app = FastAPI(title="API_TOOLS Service", version="1")

    @app.get("/healthz")
    def unavailable_health() -> dict:
        return {"ok": False, "service": "API_TOOLS", "status": "misconfigured"}

    @app.get("/readyz")
    def unavailable_ready() -> None:
        raise HTTPException(status_code=503, detail=str(configuration_error))