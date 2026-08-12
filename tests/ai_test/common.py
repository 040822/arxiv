"""共享测试基座：sys.modules 桩、Web 模块引用、FakeRequest 等。

由 test_ai_provider_config.py 拆分迁入（Q20），保持方法体逐字节一致。
"""

import importlib
import os
import sys
import types

class FakeArgs(dict):
    def get(self, key, default=None, type=None):
        value = super().get(key, default)
        if value is None:
            return default
        if type is not None:
            try:
                return type(value)
            except (TypeError, ValueError):
                return default
        return value


class FakeRequest:
    def __init__(self, data=None, endpoint="", method="POST", path="/api/test", args=None):
        self._data = data
        self.endpoint = endpoint
        self.method = method
        self.path = path
        self.full_path = path
        self.query_string = b""
        self.args = FakeArgs(args or {})

    def get_json(self, *args, **kwargs):
        return self._data


class DummyOpenAI:
    models_response = types.SimpleNamespace(data=[])
    models_error = None
    chat_response = None
    last_chat_kwargs = None
    last_init_kwargs = None

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        DummyOpenAI.last_init_kwargs = kwargs
        self.models = types.SimpleNamespace(list=self._list_models)
        self.chat = types.SimpleNamespace(
            completions=types.SimpleNamespace(create=self._create_chat_completion)
        )

    def _list_models(self):
        if DummyOpenAI.models_error:
            raise DummyOpenAI.models_error
        return DummyOpenAI.models_response

    def _create_chat_completion(self, **kwargs):
        DummyOpenAI.last_chat_kwargs = kwargs
        return DummyOpenAI.chat_response


class DummyHttpxClient:
    last_kwargs = None

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        DummyHttpxClient.last_kwargs = kwargs


def install_import_stubs():
    os.environ.setdefault("FLASK_SECRET_KEY", "test-secret-key")

    sched_mod = types.ModuleType("apscheduler.schedulers.background")

    class FakeScheduler:
        running = False

        def add_job(self, *args, **kwargs):
            pass

        def remove_job(self, *args, **kwargs):
            pass

        def get_job(self, *args, **kwargs):
            return None

        def start(self):
            self.running = True
            pass

        def get_jobs(self):
            return []

    sched_mod.BackgroundScheduler = FakeScheduler
    sys.modules.setdefault("apscheduler", types.ModuleType("apscheduler"))
    sys.modules.setdefault("apscheduler.schedulers", types.ModuleType("apscheduler.schedulers"))
    sys.modules.setdefault("apscheduler.schedulers.background", sched_mod)

    sys.modules.setdefault("arxiv", types.ModuleType("arxiv"))
    sys.modules.setdefault("fitz", types.ModuleType("fitz"))

    openai_mod = types.ModuleType("openai")
    openai_mod.OpenAI = DummyOpenAI
    openai_mod.DefaultHttpxClient = DummyHttpxClient
    sys.modules["openai"] = openai_mod
    client_mod = sys.modules.get("source.analysis.client")
    if client_mod is not None:
        client_mod.OpenAI = DummyOpenAI
        client_mod.DefaultHttpxClient = DummyHttpxClient


def _plain_jsonify(*args, **kwargs):
    if args and kwargs:
        return {"args": args, **kwargs}
    if kwargs:
        return kwargs
    if len(args) == 1:
        return args[0]
    return list(args)


def setup_web_test_base():
    """启用共享 Web 测试基座；支持嵌套调用（深度计数，最后一次 teardown 才还原）。

    嵌套支持允许随机顺序下多个测试类交叠使用同一 app context，
    避免顺序耦合导致的 "not torn down" 级联错误。
    """
    global _WEB_APP, _WEB_APP_CONTEXT, _ORIGINAL_JSONIFY, _WEB_BASE_DEPTH
    global web_application, web_auth, web_pages, web_papers_api
    global web_providers_api, web_settings_api, web_tasks_api, web_learning_api
    if _WEB_APP_CONTEXT is not None:
        _WEB_BASE_DEPTH += 1
        return
    install_import_stubs()
    web_application = importlib.import_module("source.web.application")
    web_auth = importlib.import_module("source.web.auth")
    web_benchmark_api = importlib.import_module("source.web.benchmark_api")
    web_learning_api = importlib.import_module("source.web.learning_api")
    web_pages = importlib.import_module("source.web.pages")
    web_papers_api = importlib.import_module("source.web.papers_api")
    web_providers_api = importlib.import_module("source.web.providers_api")
    web_settings_api = importlib.import_module("source.web.settings_api")
    web_tasks_api = importlib.import_module("source.web.tasks_api")
    _WEB_APP = web_application.app
    _WEB_APP_CONTEXT = _WEB_APP.app_context()
    _WEB_APP_CONTEXT.push()
    for mod in (
        web_auth, web_pages, web_papers_api, web_providers_api,
        web_settings_api, web_tasks_api, web_learning_api, web_benchmark_api,
    ):
        if mod is not None:
            _ORIGINAL_JSONIFY[mod] = mod.jsonify
            mod.jsonify = _plain_jsonify
    _WEB_BASE_DEPTH = 1


def teardown_web_test_base():
    global _WEB_APP, _WEB_APP_CONTEXT, _ORIGINAL_JSONIFY, _WEB_BASE_DEPTH
    if _WEB_APP_CONTEXT is None:
        return
    _WEB_BASE_DEPTH -= 1
    if _WEB_BASE_DEPTH > 0:
        return  # 仍有外层测试类在使用共享基座
    for mod, original_jsonify in _ORIGINAL_JSONIFY.items():
        mod.jsonify = original_jsonify
    _ORIGINAL_JSONIFY.clear()
    context = _WEB_APP_CONTEXT
    _WEB_APP_CONTEXT = None
    try:
        if context is not None:
            context.pop()
    finally:
        _WEB_APP = None


pipeline_orchestrator = None
pipeline_scheduler = None
web_application = None
web_auth = None
web_benchmark_api = None
web_learning_api = None
web_pages = None
web_papers_api = None
web_providers_api = None
web_settings_api = None
web_tasks_api = None
_WEB_APP = None
_WEB_APP_CONTEXT = None
_WEB_BASE_DEPTH = 0
_ORIGINAL_JSONIFY = {}
