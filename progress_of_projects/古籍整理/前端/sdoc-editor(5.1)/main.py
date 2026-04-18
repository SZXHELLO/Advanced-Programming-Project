from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
import requests
from PIL import Image
import io
import base64

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True  # 开发环境启用，生产环境建议关闭
    )

app = FastAPI()

# 跨域配置（开发环境允许所有域名，生产环境需限定前端域名）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -------------------------- 配置云API（以百度古籍OCR为例） --------------------------
BAIDU_OCR_API_KEY = "5eKw6lhNjDYAXy9gzUpCzzVY"       # 替换为自己的密钥
BAIDU_OCR_SECRET_KEY = "1Ha45vQKrVyFney2oG6anMAoSEZm37VP"

# 获取百度OCR Access Token
def get_baidu_token():
    url = "https://aip.baidubce.com/oauth/2.0/token"
    params = {
        "grant_type": "client_credentials",
        "client_id": BAIDU_OCR_API_KEY,
        "client_secret": BAIDU_OCR_SECRET_KEY
    }
    return requests.get(url, params=params).json().get("access_token")

# -------------------------- 接口1：AI识别古籍文字（返回单字+坐标） --------------------------
@app.post("/api/recognize-text")
async def recognize_text(image: UploadFile = File(...), page: int = 1):
    # 1. 读取前端上传的图片
    img_bytes = await image.read()
    
    # 2. 调用百度古籍OCR API
    token = get_baidu_token()
    ocr_url = f"https://aip.baidubce.com/rest/2.0/ocr/v1/accurate?access_token={token}"
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    img_base64 = base64.b64encode(img_bytes).decode('utf-8')
    data = {"image": img_base64, "recognize_granularity": "small"}  # 识别到单字
    
    # 3. 解析OCR结果，转为前端适配的格式
    ocr_result = requests.post(ocr_url, headers=headers, data=data).json()
    if "words_result" not in ocr_result:
        return {"words": [], "total": 0}
    
    words = []
    for line in ocr_result["words_result"]:
        # 单字级解析（百度返回的chars包含每个字的坐标）
        if "chars" in line:
            for char in line["chars"]:
                loc = char["location"]
                words.append({
                    "text": char["char"],    # 识别的单字
                    "x": loc["left"],        # 左上角x
                    "y": loc["top"],         # 左上角y
                    "width": loc["width"],   # 宽度
                    "height": loc["height"]  # 高度
                })
        # 整行解析（备用）
        else:
            loc = line["location"]
            words.append({
                "text": line["word"],
                "x": loc["left"],
                "y": loc["top"],
                "width": loc["width"],
                "height": loc["height"]
            })
    
    return {"words": words, "total": len(words)}

# -------------------------- 接口2：AI划分图文区域（演示版/进阶版） --------------------------
@app.post("/api/segment-regions")
async def segment_regions(image: UploadFile = File(...)):
    # 读取图片
    img_bytes = await image.read()
    img = Image.open(io.BytesIO(img_bytes))
    img_w, img_h = img.size
    
    # 【入门版】模拟区域划分（实际需替换为YOLOv8等模型）
    regions = [
        {"type": "text", "x": 0, "y": 0, "width": int(img_w*0.7), "height": img_h},  # 文字区
        {"type": "image", "x": int(img_w*0.7), "y": 0, "width": int(img_w*0.3), "height": img_h}  # 图片区
    ]
    
    # 【进阶版】YOLOv8图文区域检测（需训练自定义模型）
    # from ultralytics import YOLO
    # model = YOLO("yolov8_guji_region.pt")  # 训练好的模型
    # yolo_result = model(img)
    # regions = []
    # for r in yolo_result:
    #     for box in r.boxes:
    #         x1, y1, x2, y2 = map(int, box.xyxy[0])
    #         cls = int(box.cls)
    #         regions.append({
    #             "type": "text" if cls == 0 else "image",
    #             "x": x1, "y": y1, "width": x2-x1, "height": y2-y1
    #         })
    
    return {"regions": regions}

# 启动服务：uvicorn main:app --host 0.0.0.0 --port 8000 --reload