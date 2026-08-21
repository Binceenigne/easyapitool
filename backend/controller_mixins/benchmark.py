from __future__ import annotations

from ..common import *
from ..usage import image_usage_metrics, merge_reasoning_usage


BENCHMARK_MODES = ("instant", "flash", "medium", "high", "extra", "max")


class BenchmarkMixin:
    def benchmark_run(
        self,
        key_id: str,
        prompt: str,
        image_paths: list[str],
        options: dict[str, Any] | None = None,
        event_callback: Any = None,
    ) -> dict[str, Any]:
        clean_prompt = str(prompt or "").strip()
        if not clean_prompt:
            return {"ok": False, "error": "请输入 Benchmark 提示词"}
        clean_options = dict(options) if isinstance(options, dict) else {}
        benchmark_id = str(
            clean_options.get("benchmarkId") or f"benchmark-{uuid.uuid4().hex}"
        )[:80]
        references = [str(path) for path in list(image_paths or [])[:16]]
        started_at = time.perf_counter()

        def emit(event_type: str, **details: Any) -> None:
            if event_callback is None:
                return
            try:
                event_callback(
                    {
                        "type": event_type,
                        "benchmarkId": benchmark_id,
                        **details,
                    }
                )
            except Exception:
                pass

        emit("benchmark_started", prompt=clean_prompt)

        def run_mode(mode: str) -> dict[str, Any]:
            mode_started_at = time.perf_counter()
            request_id = f"{benchmark_id}-{mode}"[:80]
            mode_options = {
                "requestId": request_id,
                "sessionId": request_id,
                "reasoningMode": mode,
                "imageCount": 1,
                "size": clean_options.get("size") or "1024x1024",
                "quality": clean_options.get("quality") or "auto",
                "outputPreset": clean_options.get("outputPreset") or "lossless",
                "webSearchEnabled": bool(clean_options.get("webSearchEnabled", True)),
            }

            def forward_event(event: dict[str, Any]) -> None:
                if event_callback is None:
                    return
                event_callback(
                    {
                        **event,
                        "benchmarkId": benchmark_id,
                        "benchmarkMode": mode,
                    }
                )

            result = self.generate_image(
                key_id,
                clean_prompt,
                references,
                mode_options,
                event_callback=forward_event,
            )
            items = list(result.get("items") or []) if isinstance(result, dict) else []
            image_usage: dict[str, Any] = {}
            for item in items:
                if not isinstance(item, dict) or not item.get("ok"):
                    continue
                usage = image_usage_metrics(
                    item.get("usage"),
                    "gpt-image-2",
                    str(item.get("requestedQuality") or mode_options["quality"]),
                    str(item.get("actualSize") or item.get("requestedSize") or mode_options["size"]),
                    int(item.get("partialImagesReceived") or 0),
                )
                image_usage = merge_reasoning_usage(image_usage, usage)
            reasoning_usage = dict(result.get("reasoningUsage") or {})
            combined_usage = merge_reasoning_usage(reasoning_usage, image_usage)
            benchmark_result = {
                **result,
                "benchmarkId": benchmark_id,
                "benchmarkMode": mode,
                "elapsedMs": max(1, round((time.perf_counter() - mode_started_at) * 1000)),
                "finalPrompt": str(result.get("prompt") or ""),
                "imageUsage": image_usage,
                "usage": {
                    **combined_usage,
                    "callCountBasis": "每个 API 请求",
                },
            }
            emit(
                "benchmark_usage",
                benchmarkMode=mode,
                reasoningUsage=reasoning_usage,
                imageUsage=image_usage,
                usage=benchmark_result["usage"],
                finalPrompt=benchmark_result["finalPrompt"],
                webReferences=list(result.get("webReferences") or []),
            )
            emit(
                "benchmark_mode_completed",
                benchmarkMode=mode,
                result=benchmark_result,
            )
            return benchmark_result

        results: list[dict[str, Any]] = []
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=len(BENCHMARK_MODES),
            thread_name_prefix="image-benchmark",
        ) as executor:
            futures = {
                executor.submit(run_mode, mode): mode for mode in BENCHMARK_MODES
            }
            for future in concurrent.futures.as_completed(futures):
                mode = futures[future]
                try:
                    results.append(future.result())
                except Exception as exc:
                    failed = {
                        "ok": False,
                        "benchmarkId": benchmark_id,
                        "benchmarkMode": mode,
                        "elapsedMs": 0,
                        "error": str(exc),
                        "items": [],
                    }
                    results.append(failed)
                    emit(
                        "benchmark_mode_completed",
                        benchmarkMode=mode,
                        result=failed,
                    )
        results.sort(key=lambda item: BENCHMARK_MODES.index(item["benchmarkMode"]))
        successful = sum(1 for result in results if result.get("ok"))
        emit(
            "benchmark_completed",
            successful=successful,
            elapsedMs=max(1, round((time.perf_counter() - started_at) * 1000)),
        )
        return {
            "ok": successful > 0,
            "benchmarkId": benchmark_id,
            "successful": successful,
            "results": results,
        }