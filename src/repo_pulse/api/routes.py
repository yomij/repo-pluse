from typing import Any, Dict, Optional, Protocol

from fastapi import APIRouter, BackgroundTasks

from repo_pulse.details.request_parser import extract_message_text


class ContainerProtocol(Protocol):
    def handle_event(self, payload: Dict[str, Any]) -> Any:
        ...

    def handle_action(self, payload: Dict[str, Any]) -> Any:
        ...

    def run_digest_now(self) -> Any:
        ...


def _extract_message_text(payload: Dict[str, Any]) -> Optional[str]:
    event = payload.get('event') or {}
    message = event.get('message') or {}
    text = extract_message_text(message)
    if text:
        return text
    return None


def build_router(container: Optional[ContainerProtocol] = None) -> APIRouter:
    router = APIRouter()

    @router.get('/healthz')
    async def healthz() -> Dict[str, str]:
        return {'status': 'ok'}

    @router.post('/webhooks/feishu/events')
    async def feishu_events(payload: Dict[str, Any], background_tasks: BackgroundTasks) -> Dict[str, Any]:
        challenge = payload.get('challenge')
        if challenge is not None:
            return {'challenge': challenge}

        if container is not None and _extract_message_text(payload):
            background_tasks.add_task(container.handle_event, payload)

        return {'ok': True}

    @router.post('/webhooks/feishu/actions')
    async def feishu_actions(payload: Dict[str, Any], background_tasks: BackgroundTasks) -> Dict[str, Any]:
        if container is not None:
            background_tasks.add_task(container.handle_action, payload)

        return {}

    @router.post('/internal/run-digest')
    async def run_digest(
        background_tasks: BackgroundTasks, payload: Optional[Dict[str, Any]] = None
    ) -> Dict[str, bool]:
        del payload
        if container is not None:
            background_tasks.add_task(container.run_digest_now)

        return {'queued': True}

    return router
