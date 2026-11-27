"""
So sánh hiệu năng & độ chính xác giữa bản Baseline (git HEAD `a4ae332`) và bản Optimized hiện tại
trực tiếp trên video thực tế trong thư mục `data/`.

Cách chạy:
    python testcase/benchmark_compare_baseline.py
"""
from __future__ import annotations

import argparse
import json
import os
import pickle
import shutil
import socket
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List

import cv2
import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_VIDEO_DEFAULT = next(iter(sorted((PROJECT_ROOT / "data").glob("*.mp4"))), None)
MODEL_PT = PROJECT_ROOT / "models" / "VTS_FINETUNE.pt"
MODEL_ENGINE = PROJECT_ROOT / "models" / "VTS_FINETUNE.engine"
BASELINE_REF = "a4ae332"


def _init_isolated_env(repo_root: Path, temp_db_path: Path) -> None:
    """Cấu hình sys.path và SQLite tạm thời tách biệt hoàn toàn giữa 2 phiên bản."""
    os.chdir(str(repo_root))
    root_str = str(repo_root)
    backend_str = str(repo_root / "backend")
    for p in (backend_str, root_str):
        if p in sys.path:
            sys.path.remove(p)
        sys.path.insert(0, p)

    import core.config as root_cfg
    import backend.core.config as back_cfg

    root_cfg.DATABASE_PATH = temp_db_path
    back_cfg.DATABASE_PATH = temp_db_path

    import database.sqlite_db as sqlite_db
    import backend.database.sqlite_db as b_sqlite_db
    sqlite_db.DATABASE_PATH = temp_db_path
    b_sqlite_db.DATABASE_PATH = temp_db_path
    sqlite_db.init_db()


def _default_bench_settings(model_path: Path, stride: int) -> Dict[str, Any]:
    return {
        "camera_id": 888,
        "name": "CamDataBench",
        "model_path": str(model_path),
        "confidence_threshold": 0.25,
        "enable_ai": True,
        "enable_congestion": True,
        "enable_illegal_parking": True,
        "enable_license_plate": True,
        "enable_weapon_detection": True,
        "congestion_threshold": 35.0,
        "stop_seconds": 30.0,
        "parking_move_threshold_px": 10.0,
        "process_every_n_frames": stride,
        "save_to_db": False,
        "telegram_enabled": False,
        "show_roi_surveillance": True,
        "show_roi_parking": True,
        "show_fps": True,
        "roi_points": [[200, 120], [1700, 120], [1800, 1000], [150, 1000]],
        "roi_meta": {"units": "pixels", "ref_width": 1920, "ref_height": 1085},
        "no_parking_points": [[350, 150], [1050, 150], [1050, 450], [350, 450]],
        "no_park_meta": {"units": "pixels", "ref_width": 1920, "ref_height": 1085},
    }


def _worker_e2e(
    repo_root: Path,
    video_path: Path,
    model_path: Path,
    stride: int,
    target_frames: int,
) -> Dict[str, Any]:
    import psutil

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        temp_db = Path(tmpdir) / "bench.db"
        _init_isolated_env(repo_root, temp_db)

        from backend.infrastructure.ml.detection_bridge import process_video

        proc = psutil.Process()
        t_start = time.perf_counter()
        cpu_before = proc.cpu_times()

        events: List[Dict[str, Any]] = []
        t_loading = None
        t_frame1 = None
        t_warmup = None
        warmup_pf = None
        t_end_pf = None
        end_pf = None
        total_jpeg_bytes = 0
        last_jpeg_shape = None
        last_cb_status = ""

        def on_progress(payload: Dict[str, Any]):
            nonlocal t_loading, t_frame1, t_warmup, warmup_pf, t_end_pf, end_pf
            nonlocal total_jpeg_bytes, last_jpeg_shape, last_cb_status

            now = time.perf_counter()
            phase = payload.get("phase")
            pf = int(payload.get("processed_frames", 0) or 0)
            jpg = payload.get("preview_jpeg")
            if jpg:
                total_jpeg_bytes += len(jpg)
                arr = np.frombuffer(jpg, dtype=np.uint8)
                decoded = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                if decoded is not None:
                    last_jpeg_shape = list(decoded.shape[:2])

            if phase == "loading_model" and t_loading is None:
                t_loading = now
            if pf >= 1 and t_frame1 is None:
                t_frame1 = now
            if pf >= 10 and t_warmup is None:
                t_warmup = now
                warmup_pf = pf
            if payload.get("latest_status"):
                last_cb_status = str(payload.get("latest_status"))

            events.append({"phase": phase, "pf": pf, "t": now - t_start})

            if pf >= target_frames:
                t_end_pf = now
                end_pf = pf
                return {"viewer_count": 1, "abort": True}
            return {"viewer_count": 1}

        settings = _default_bench_settings(model_path, stride)
        summary = process_video(
            input_path=str(video_path),
            settings=settings,
            progress_callback=on_progress,
        )

        t_done = time.perf_counter()
        cpu_after = proc.cpu_times()
        mem = proc.memory_info()
        peak_rss_mb = getattr(mem, "peak_wset", mem.rss) / (1024 * 1024)
        cpu_used = (cpu_after.user - cpu_before.user) + (cpu_after.system - cpu_before.system)

        steady_frames = (end_pf - warmup_pf) if (end_pf and warmup_pf and end_pf > warmup_pf) else 0
        steady_s = (t_end_pf - t_warmup) if (t_end_pf and t_warmup and t_end_pf > t_warmup) else 0.0
        steady_fps = (steady_frames / steady_s) if steady_s > 0 else 0.0

        return {
            "total_wall_s": round(t_done - t_start, 3),
            "startup_to_loading_s": round((t_loading - t_start), 3) if t_loading else None,
            "loading_to_frame1_s": round((t_frame1 - (t_loading or t_start)), 3) if t_frame1 else None,
            "startup_to_frame1_s": round((t_frame1 - t_start), 3) if t_frame1 else None,
            "steady_frames": steady_frames,
            "steady_s": round(steady_s, 3),
            "steady_fps": round(steady_fps, 2),
            "callback_count": len(events),
            "total_jpeg_kb": round(total_jpeg_bytes / 1024.0, 1),
            "preview_shape": last_jpeg_shape,
            "cpu_seconds": round(cpu_used, 3),
            "peak_rss_mb": round(peak_rss_mb, 1),
            "summary": summary,
            "last_cb_status": last_cb_status,
        }


def _worker_micro(repo_root: Path, fixture_path: Path, clip_path: Path, is_optimized: bool) -> Dict[str, Any]:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        temp_db = Path(tmpdir) / "micro.db"
        _init_isolated_env(repo_root, temp_db)

        import torch
        from modules.traffic.traffic_monitor import TrafficMonitor
        from modules.parking.tracking_stabilizer import StationaryVehicleStabilizer
        from modules.ocr.ocr_manager import OCRManager
        import backend.infrastructure.ml.detection_bridge as bridge

        with open(fixture_path, "rb") as f:
            payload = pickle.load(f)

        frames_1080: List[np.ndarray] = payload["frames"]
        det_frames: List[Dict[str, Any]] = payload["detections"]
        h, w = frames_1080[0].shape[:2]

        # 1. TrafficMonitor (calculate_speed_and_status + draw_status) trên 200 frame 1080p thật
        roi_poly = np.array([[200, 120], [1700, 120], [1800, 1000], [150, 1000]], dtype=np.int32)
        tm = TrafficMonitor(roi_polygon=roi_poly, congestion_threshold=35.0)
        tm_levels: List[int] = []
        tm_occs: List[float] = []
        draw_canvas = frames_1080[0].copy()

        # Warmup 3 frames
        for i in range(3):
            tm.reset_counters()
            d = det_frames[i]
            for j in range(len(d["cls"])):
                lbl = d["names"][int(d["cls"][j])]
                x1, y1, x2, y2 = d["xyxy"][j]
                tid = int(d["ids"][j]) if d["ids"] is not None else j + 1
                cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
                if lbl == "person":
                    tm.log_person(bbox=(x1, y1, x2, y2))
                elif lbl in ("car", "motorcycle", "bus", "truck", "bicycle"):
                    tm.log_vehicle(tid, cx, cy, 1.0 + i * 0.04, bbox=(x1, y1, x2, y2))
            spd, st_txt, st_clr, lvl = tm.calculate_speed_and_status(1.0 + i * 0.04, draw_canvas.shape)
            tm.draw_status(draw_canvas, spd, st_txt, st_clr, 1.2, 2, is_admin=True)

        t0 = time.perf_counter()
        for i, d in enumerate(det_frames):
            tm.reset_counters()
            np.copyto(draw_canvas, frames_1080[i])
            cur_t = 10.0 + i * 0.04
            for j in range(len(d["cls"])):
                lbl = d["names"][int(d["cls"][j])]
                x1, y1, x2, y2 = d["xyxy"][j]
                tid = int(d["ids"][j]) if d["ids"] is not None else j + 1
                cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
                if lbl == "person":
                    tm.log_person(bbox=(x1, y1, x2, y2))
                elif lbl in ("car", "motorcycle", "bus", "truck", "bicycle"):
                    tm.log_vehicle(tid, cx, cy, cur_t, bbox=(x1, y1, x2, y2))
            spd, st_txt, st_clr, lvl = tm.calculate_speed_and_status(cur_t, draw_canvas.shape)
            tm.draw_status(draw_canvas, spd, st_txt, st_clr, 1.2, 2, is_admin=True)
            tm_levels.append(int(lvl))
            tm_occs.append(round(float(tm.last_occupancy), 3))
        tm_elapsed_s = time.perf_counter() - t0

        # 2. Stabilizer duplicate suppression trên 200 frame detections thật
        stab = StationaryVehicleStabilizer()
        suppressed_per_frame: List[List[int]] = []
        t0 = time.perf_counter()
        if is_optimized and hasattr(stab, "filter_duplicate_indices_np"):
            for d in det_frames:
                xyxy_np = d["xyxy"]
                conf_np = d["conf"]
                veh_indices = [
                    j for j, c in enumerate(d["cls"])
                    if d["names"][int(c)] in ("car", "bus", "truck")
                ]
                sup = stab.filter_duplicate_indices_np(xyxy_np, conf_np, veh_indices)
                suppressed_per_frame.append(sorted(sup))
        else:
            # Tái lập đúng hành vi baseline: cắt tensor theo từng box
            for d in det_frames:
                t_xyxy = torch.from_numpy(d["xyxy"].astype(np.float32))
                t_conf = torch.from_numpy(d["conf"].astype(np.float32))
                box_proxies = [
                    type("_B", (), {"xyxy": t_xyxy[j : j + 1], "conf": t_conf[j : j + 1]})()
                    for j in range(len(d["cls"]))
                ]
                veh_indices = [
                    j for j, c in enumerate(d["cls"])
                    if d["names"][int(c)] in ("car", "bus", "truck")
                ]
                sup = stab.filter_duplicate_indices(box_proxies, veh_indices)
                suppressed_per_frame.append(sorted(sup))
        stab_elapsed_s = time.perf_counter() - t0

        # 3. VideoStream cold probe + giải mã 150 frame clip cắt từ data video
        t0 = time.perf_counter()
        if is_optimized and hasattr(bridge, "_probe_stream_metadata"):
            pw, ph, pfps, ptot, is_h = bridge._probe_stream_metadata(clip_path)
            vs = bridge.VideoStream(clip_path, force_single_thread=is_h, pre_probed=(pw, ph, pfps, ptot)).start()
        else:
            is_h = bridge._is_hevc(clip_path)
            vs = bridge.VideoStream(clip_path, force_single_thread=is_h).start()

        decoded_count = 0
        first_frame_checksum = None
        t_first_read = None
        for _ in range(150):
            ret, fr = vs.read()
            if not ret or fr is None:
                break
            if t_first_read is None:
                t_first_read = time.perf_counter() - t0
                first_frame_checksum = int(fr.sum())
            decoded_count += 1
        vs.release()
        decode_elapsed_s = time.perf_counter() - t0

        # 4. OCRManager default server mode flag
        ocr_mgr = OCRManager(reader=None)
        ocr_debug_default = bool(getattr(ocr_mgr, "enable_debug_window", True))

        return {
            "num_frames": len(det_frames),
            "frame_shape": [h, w],
            "traffic_monitor_s": round(tm_elapsed_s, 4),
            "traffic_monitor_ms_per_frame": round((tm_elapsed_s / len(det_frames)) * 1000.0, 3),
            "traffic_monitor_fps": round(len(det_frames) / tm_elapsed_s, 1),
            "tm_levels": tm_levels,
            "tm_occs": tm_occs,
            "stabilizer_s": round(stab_elapsed_s, 5),
            "stabilizer_ms_total": round(stab_elapsed_s * 1000.0, 2),
            "suppressed_per_frame": suppressed_per_frame,
            "decode_frames": decoded_count,
            "decode_startup_to_first_s": round(t_first_read or 0.0, 3),
            "decode_total_s": round(decode_elapsed_s, 3),
            "decode_fps": round(decoded_count / decode_elapsed_s, 1) if decode_elapsed_s > 0 else 0.0,
            "first_frame_checksum": first_frame_checksum,
            "ocr_debug_default": ocr_debug_default,
        }


def _prepare_micro_fixtures(video_path: Path, model_path: Path, out_dir: Path, num_frames: int = 200):
    """Giải mã sẵn 200 frame 1080p thật + chạy YOLO 1 lần để làm input cố định cho cả Baseline & Optimized."""
    from ultralytics import YOLO

    clip_path = out_dir / "data_clip_15s.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-i", str(video_path),
            "-t", "12",
            "-c", "copy",
            "-movflags", "+faststart",
            str(clip_path),
        ],
        check=True,
    )

    cap = cv2.VideoCapture(str(clip_path))
    model = YOLO(str(model_path))
    frames_1080: List[np.ndarray] = []
    detections: List[Dict[str, Any]] = []

    while len(frames_1080) < num_frames:
        ok, fr = cap.read()
        if not ok or fr is None:
            break
        h, w = fr.shape[:2]
        if w > 1920:
            nh = int(h * (1920 / w))
            nh += nh % 2
            fr = cv2.resize(fr, (1920, nh))
        frames_1080.append(fr)

    cap.release()

    for fr in frames_1080:
        res = model.track(fr, persist=True, tracker="bytetrack.yaml", verbose=False, imgsz=640)[0]
        boxes = res.boxes
        if boxes is not None and len(boxes) > 0:
            xyxy = boxes.xyxy.cpu().numpy().astype(np.int32)
            conf = boxes.conf.cpu().numpy().astype(np.float32)
            cls_arr = boxes.cls.cpu().numpy().astype(np.int32)
            ids = boxes.id.cpu().numpy().astype(np.int32) if boxes.id is not None else None
        else:
            xyxy = np.empty((0, 4), dtype=np.int32)
            conf = np.empty((0,), dtype=np.float32)
            cls_arr = np.empty((0,), dtype=np.int32)
            ids = None
        detections.append({
            "xyxy": xyxy,
            "conf": conf,
            "cls": cls_arr,
            "ids": ids,
            "names": dict(model.names),
        })

    fixture_path = out_dir / "micro_fixture_200.pkl"
    with open(fixture_path, "wb") as f:
        pickle.dump({"frames": frames_1080, "detections": detections}, f, protocol=pickle.HIGHEST_PROTOCOL)

    return fixture_path, clip_path


def _create_baseline_worktree(wt_dir: Path, ref: str) -> None:
    if wt_dir.exists():
        subprocess.run(["git", "worktree", "remove", "--force", str(wt_dir)], cwd=str(PROJECT_ROOT), capture_output=True)
        shutil.rmtree(wt_dir, ignore_errors=True)
    subprocess.run(
        ["git", "worktree", "add", "--detach", str(wt_dir), ref],
        cwd=str(PROJECT_ROOT),
        check=True,
        capture_output=True,
    )
    for folder in ("models", "trt_cache", "bin"):
        src = PROJECT_ROOT / folder
        dst = wt_dir / folder
        if src.exists() and not dst.exists():
            subprocess.run(["cmd", "/c", "mklink", "/J", str(dst), str(src)], check=True, capture_output=True)


def _remove_baseline_worktree(wt_dir: Path) -> None:
    for folder in ("models", "trt_cache", "bin"):
        dst = wt_dir / folder
        if dst.exists():
            subprocess.run(["cmd", "/c", "rmdir", str(dst)], capture_output=True)
    subprocess.run(["git", "worktree", "remove", "--force", str(wt_dir)], cwd=str(PROJECT_ROOT), capture_output=True)
    shutil.rmtree(wt_dir, ignore_errors=True)


def _is_port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.4):
            return True
    except OSError:
        return False


def _start_temp_mediamtx():
    """Khởi động tạm MediaMTX để benchmark E2E RTSP hoạt động bình thường trên cả Baseline và Optimized."""
    if _is_port_open("127.0.0.1", 8554):
        return None
    exe = PROJECT_ROOT / "bin" / "mediamtx" / ("mediamtx.exe" if os.name == "nt" else "mediamtx")
    if not exe.exists():
        return None
    proc = subprocess.Popen(
        [str(exe)],
        cwd=str(exe.parent),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.time() + 6.0
    while time.time() < deadline:
        if _is_port_open("127.0.0.1", 8554):
            return proc
        if proc.poll() is not None:
            return None
        time.sleep(0.1)
    return proc


def _stop_temp_mediamtx(proc) -> None:
    if proc is None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=3.0)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _run_subprocess(args: List[str], timeout_s: int = 120) -> Dict[str, Any]:
    proc = subprocess.Popen(
        [sys.executable, __file__] + args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
        else:
            proc.kill()
        stdout, stderr = proc.communicate()
        raise RuntimeError(f"Worker quá hạn {timeout_s}s ({args}):\n{stdout[-800:]}\n{stderr[-800:]}")
    if proc.returncode != 0:
        raise RuntimeError(f"Worker thất bại ({args}):\n{stdout}\n{stderr}")
    for line in reversed(stdout.splitlines()):
        if line.startswith("BENCH_JSON:"):
            return json.loads(line[len("BENCH_JSON:"):])
    raise RuntimeError(f"Không tìm thấy BENCH_JSON trong output:\n{stdout}\n{stderr}")


def _pct_gain(old_val: float, new_val: float, higher_is_better: bool = True) -> str:
    if old_val <= 0 or new_val <= 0:
        return "N/A"
    if higher_is_better:
        ratio = new_val / old_val
        pct = ((new_val - old_val) / old_val) * 100.0
        return f"{pct:+.1f}% ({ratio:.2f}x)"
    pct = ((old_val - new_val) / old_val) * 100.0
    ratio = old_val / new_val
    return f"-{pct:.1f}% ({ratio:.2f}x nhanh hơn)"


def run_comparison(video_path: Path, baseline_ref: str, target_frames: int) -> Dict[str, Any]:
    if not video_path.exists():
        raise FileNotFoundError(f"Không tìm thấy video: {video_path}")

    mediamtx_proc = _start_temp_mediamtx()
    with tempfile.TemporaryDirectory(prefix="datn_ab_", ignore_cleanup_errors=True) as tmp:
        tmp_path = Path(tmp)
        wt_dir = tmp_path / "baseline_wt"
        _create_baseline_worktree(wt_dir, baseline_ref)
        try:
            print(f"[Benchmark] Đang chuẩn bị fixture 200 frame thật từ {video_path.name}...", flush=True)
            fixture_path, clip_path = _prepare_micro_fixtures(video_path, MODEL_PT, tmp_path, num_frames=200)

            print("[Benchmark] 1/4 Chạy Microbenchmark thuần CPU/RAM + đối soát sai số trên 200 frame thật...", flush=True)
            micro_base = _run_subprocess([
                "--worker", "micro",
                "--repo-root", str(wt_dir),
                "--fixture", str(fixture_path),
                "--clip", str(clip_path),
                "--optimized", "0",
            ])
            micro_opt = _run_subprocess([
                "--worker", "micro",
                "--repo-root", str(PROJECT_ROOT),
                "--fixture", str(fixture_path),
                "--clip", str(clip_path),
                "--optimized", "1",
            ])

            occ_diffs = [abs(a - b) for a, b in zip(micro_base["tm_occs"], micro_opt["tm_occs"])]
            level_matches = sum(1 for a, b in zip(micro_base["tm_levels"], micro_opt["tm_levels"]) if a == b)
            stab_matches = sum(
                1 for a, b in zip(micro_base["suppressed_per_frame"], micro_opt["suppressed_per_frame"]) if a == b
            )

            e2e_cases = [
                ("PT_stride2_full_video", MODEL_PT, 2),
                ("PT_stride1_full_video", MODEL_PT, 1),
                ("TRT_stride1_full_video", MODEL_ENGINE, 1),
            ]
            e2e_results = {}
            for idx, (case_name, m_path, stride) in enumerate(e2e_cases, start=2):
                if not m_path.exists():
                    continue
                print(f"[Benchmark] {idx}/4 Chạy E2E '{case_name}' ({target_frames} frames) trên Baseline vs Optimized...", flush=True)
                base_res = _run_subprocess([
                    "--worker", "e2e",
                    "--repo-root", str(wt_dir),
                    "--video", str(video_path),
                    "--model", str(m_path),
                    "--stride", str(stride),
                    "--frames", str(target_frames),
                ])
                time.sleep(0.8)
                opt_res = _run_subprocess([
                    "--worker", "e2e",
                    "--repo-root", str(PROJECT_ROOT),
                    "--video", str(video_path),
                    "--model", str(m_path),
                    "--stride", str(stride),
                    "--frames", str(target_frames),
                ])
                time.sleep(0.8)
                e2e_results[case_name] = {"baseline": base_res, "optimized": opt_res}

            report = {
                "video": str(video_path.name),
                "baseline_ref": baseline_ref,
                "micro": {
                    "baseline": {k: v for k, v in micro_base.items() if k not in ("tm_levels", "tm_occs", "suppressed_per_frame")},
                    "optimized": {k: v for k, v in micro_opt.items() if k not in ("tm_levels", "tm_occs", "suppressed_per_frame")},
                    "accuracy_parity": {
                        "frames": len(occ_diffs),
                        "traffic_occ_mean_diff_pct": round(statistics.mean(occ_diffs), 3),
                        "traffic_occ_max_diff_pct": round(max(occ_diffs), 3),
                        "traffic_level_match_pct": round((level_matches / len(occ_diffs)) * 100.0, 2),
                        "stabilizer_exact_match_frames": f"{stab_matches}/{len(occ_diffs)}",
                        "decode_pixel_checksum_equal": micro_base["first_frame_checksum"] == micro_opt["first_frame_checksum"],
                    },
                },
                "e2e": e2e_results,
            }
            print("\n=== TÓM TẮT SO SÁNH BEFORE (a4ae332) vs AFTER (Optimized) ===")
            print(f"TrafficMonitor (200f 1080p): {micro_base['traffic_monitor_fps']} FPS -> {micro_opt['traffic_monitor_fps']} FPS | {_pct_gain(micro_base['traffic_monitor_fps'], micro_opt['traffic_monitor_fps'])}")
            print(f"Sai số occ mask 1/4: max={report['micro']['accuracy_parity']['traffic_occ_max_diff_pct']}%, mean={report['micro']['accuracy_parity']['traffic_occ_mean_diff_pct']}%, level khớp={report['micro']['accuracy_parity']['traffic_level_match_pct']}%")
            print(f"Stabilizer (200f): {micro_base['stabilizer_ms_total']}ms -> {micro_opt['stabilizer_ms_total']}ms | {_pct_gain(micro_base['stabilizer_ms_total'], micro_opt['stabilizer_ms_total'], False)} | khớp {stab_matches}/{len(occ_diffs)}")
            print(f"FFmpeg decode (150f): {micro_base['decode_total_s']}s -> {micro_opt['decode_total_s']}s (startup: {micro_base['decode_startup_to_first_s']}s -> {micro_opt['decode_startup_to_first_s']}s)")
            for cname, pair in e2e_results.items():
                b, o = pair["baseline"], pair["optimized"]
                print(
                    f"[{cname}] Wall: {b['total_wall_s']}s -> {o['total_wall_s']}s | "
                    f"Steady FPS: {b['steady_fps']} -> {o['steady_fps']} | "
                    f"Callbacks: {b['callback_count']} -> {o['callback_count']} | "
                    f"CPU: {b['cpu_seconds']}s -> {o['cpu_seconds']}s | "
                    f"RAM peak: {b['peak_rss_mb']}MB -> {o['peak_rss_mb']}MB | "
                    f"Out shape: {b['preview_shape']} vs {o['preview_shape']} | "
                    f"Status: '{b['summary'].get('latest_status')}' vs '{o['summary'].get('latest_status')}'"
                )
            print("FINAL_REPORT_JSON:" + json.dumps(report, ensure_ascii=False))
            return report
        finally:
            _stop_temp_mediamtx(mediamtx_proc)
            _remove_baseline_worktree(wt_dir)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", choices=["e2e", "micro"], default=None)
    parser.add_argument("--repo-root", type=str)
    parser.add_argument("--video", type=str, default=str(DATA_VIDEO_DEFAULT) if DATA_VIDEO_DEFAULT else "")
    parser.add_argument("--model", type=str)
    parser.add_argument("--stride", type=int, default=2)
    parser.add_argument("--frames", type=int, default=200)
    parser.add_argument("--fixture", type=str)
    parser.add_argument("--clip", type=str)
    parser.add_argument("--optimized", type=int, default=1)
    parser.add_argument("--baseline-ref", type=str, default=BASELINE_REF)
    args = parser.parse_args()

    if args.worker == "e2e":
        res = _worker_e2e(
            Path(args.repo_root),
            Path(args.video),
            Path(args.model),
            args.stride,
            args.frames,
        )
        print("BENCH_JSON:" + json.dumps(res, ensure_ascii=False))
    elif args.worker == "micro":
        res = _worker_micro(
            Path(args.repo_root),
            Path(args.fixture),
            Path(args.clip),
            bool(args.optimized),
        )
        print("BENCH_JSON:" + json.dumps(res, ensure_ascii=False))
    else:
        run_comparison(Path(args.video), args.baseline_ref, args.frames)


if __name__ == "__main__":
    main()
