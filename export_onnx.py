import os
import urllib.request
import tarfile
import subprocess
import shutil
import sys

MODELS = {
    "det": {
        "url": "https://paddleocr.bj.bcebos.com/PP-OCRv3/english/en_PP-OCRv3_det_infer.tar",
        "folder": "en_PP-OCRv3_det_infer",
        "shape": "{'x':[-1,3,-1,-1]}"
    },
    "rec": {
        "url": "https://paddleocr.bj.bcebos.com/PP-OCRv4/english/en_PP-OCRv4_rec_infer.tar",
        "folder": "en_PP-OCRv4_rec_infer",
        "shape": "{'x':[-1,3,48,-1]}"
    },
    "cls": {
        "url": "https://paddleocr.bj.bcebos.com/dygraph_v2.0/ch/ch_ppocr_mobile_v2.0_cls_infer.tar",
        "folder": "ch_ppocr_mobile_v2.0_cls_infer",
        "shape": "{'x':[-1,3,48,192]}"
    }
}

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODELS_ONNX_DIR = os.path.join(BASE_DIR, "models", "paddle_onnx")
TEMP_DIR = os.path.join(BASE_DIR, "temp_models")

def download_and_extract(url, extract_to):
    print(f"[*] Downloading {url}...")
    filename = url.split("/")[-1]
    filepath = os.path.join(extract_to, filename)
    urllib.request.urlretrieve(url, filepath)
    
    print(f"[*] Extracting {filename}...")
    with tarfile.open(filepath, "r") as tar:
        tar.extractall(path=extract_to)
    
    os.remove(filepath)

def export_to_onnx(model_type, config):
    pd_model_dir = os.path.join(TEMP_DIR, config["folder"])
    save_dir = os.path.join(MODELS_ONNX_DIR, model_type)
    os.makedirs(save_dir, exist_ok=True)
    
    save_file = os.path.join(save_dir, "model.onnx")
    
    paddle2onnx_exe = os.path.join(os.path.dirname(sys.executable), "Scripts", "paddle2onnx.exe")
    if not os.path.exists(paddle2onnx_exe):
        # Fallback in case it's not in Scripts (e.g. Linux or different env setup)
        paddle2onnx_exe = "paddle2onnx"

    cmd = [
        paddle2onnx_exe,
        "--model_dir", pd_model_dir,
        "--model_filename", "inference.pdmodel",
        "--params_filename", "inference.pdiparams",
        "--save_file", save_file,
        "--opset_version", "11",
        "--enable_onnx_checker", "True"
    ]
    
    print(f"\n[*] Exporting {model_type.upper()} model to ONNX...")
    print(" ".join(cmd))
    subprocess.run(cmd, check=True)
    print(f"[+] Saved at: {save_file}")

def main():
    print("=== AUTO DOWNLOAD AND EXPORT PADDLE TO ONNX ===")
    
    # 1. Cài đặt paddle2onnx nếu chưa có
    print("[!] Ensuring cmake and correct paddle2onnx version are installed...")
    subprocess.run([sys.executable, "-m", "pip", "install", "cmake"], check=True)
    subprocess.run([sys.executable, "-m", "pip", "install", "paddle2onnx==1.3.1"], check=True)
    
    # 2. Tạo thư mục tạm
    os.makedirs(TEMP_DIR, exist_ok=True)
    
    # 3. Tải, giải nén và chuyển đổi
    try:
        for m_type, m_config in MODELS.items():
            download_and_extract(m_config["url"], TEMP_DIR)
            export_to_onnx(m_type, m_config)
            
    except Exception as e:
        print(f"[-] Error occurred: {e}")
    finally:
        # 4. Dọn dẹp thư mục tạm
        print("\n[*] Cleaning up temporary files...")
        shutil.rmtree(TEMP_DIR, ignore_errors=True)
        print("=== DONE! YOU NOW HAVE ALL 3 ONNX FILES ===")

if __name__ == "__main__":
    main()
