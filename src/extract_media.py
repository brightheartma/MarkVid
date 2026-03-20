import cv2
from moviepy import VideoFileClip
import os
import datetime
import concurrent.futures # 新增：并发库
from tqdm import tqdm

def format_timestamp(seconds):
    """将总秒数格式化为 HH:MM:SS"""
    return str(datetime.timedelta(seconds=int(seconds)))

def extract_from_video(video_path, output_dir, frame_interval=60):
    """从单个视频中提取音频和带精确时间戳的关键帧"""
    os.makedirs(output_dir, exist_ok=True)
    
    try:
        # 1. 提取音频
        video_clip = VideoFileClip(video_path)
        audio_path = os.path.join(output_dir, "audio.mp3")
        if video_clip.audio:
            video_clip.audio.write_audiofile(audio_path, logger=None)
        video_clip.close()

        # 2. 提取画面帧并附加时间戳
        cap = cv2.VideoCapture(video_path)
        fps = cap.get(cv2.CAP_PROP_FPS)
        
        if fps == 0:
            fps = 30 
            
        count = 0
        saved_count = 0

        while cap.isOpened():
            ret, frame = cap.read()
            if not ret:
                break
                
            if count % frame_interval == 0:
                current_sec = count / fps
                time_str = format_timestamp(current_sec).replace(':', '-')
                
                file_name = f"frame_{saved_count:04d}_{time_str}.jpg"
                frame_path = os.path.join(output_dir, file_name)
                
                cv2.imwrite(frame_path, frame)
                saved_count += 1
            count += 1

        cap.release()
        return f"成功: 提取 {saved_count} 张图和 1 个音频。" # 改为 return，便于多进程捕获结果
        
    except Exception as e:
        return f"失败: {str(e)}"

def _already_extracted(output_dir: str) -> bool:
    """判断该视频是否已提取过（audio.mp3 + 至少一张关键帧均存在）"""
    if not os.path.isdir(output_dir):
        return False
    if not os.path.exists(os.path.join(output_dir, "audio.mp3")):
        return False
    for f in os.listdir(output_dir):
        if f.startswith("frame_") and f.endswith(".jpg"):
            return True
    return False

def batch_process_videos_concurrent(input_folder, output_base_folder, frame_interval=30, max_workers=None):
    """加入增量跳过 + tqdm 进度条的并发处理逻辑"""
    if not os.path.exists(input_folder):
        print(f"错误：输入文件夹 '{input_folder}' 不存在。")
        return

    valid_extensions = ('.mp4', '.mov', '.avi', '.mkv')
    tasks = []
    skipped = []

    # 1. 收集任务，增量跳过已提取的视频
    for file_name in sorted(os.listdir(input_folder)):
        if file_name.lower().endswith(valid_extensions):
            video_path = os.path.join(input_folder, file_name)
            base_name = os.path.splitext(file_name)[0]
            output_dir = os.path.join(output_base_folder, base_name)
            if _already_extracted(output_dir):
                skipped.append(file_name)
            else:
                tasks.append((video_path, output_dir, frame_interval))

    total = len(tasks) + len(skipped)
    if total == 0:
        print("未在目录中找到支持的视频文件。")
        return

    # 2. 打印跳过摘要（不逐条列出）
    if skipped:
        print(f"⏭️  已跳过 {len(skipped)} 个（已提取，增量模式）")

    if not tasks:
        print(f"✅ 全部 {total} 个视频均已提取，无需处理。")
        return

    print(f"🚀 新增 {len(tasks)} 个视频，开始多进程并发处理...\n")

    # 3. 并发执行与进度追踪
    with concurrent.futures.ProcessPoolExecutor(max_workers=max_workers) as executor:
        future_to_video = {
            executor.submit(extract_from_video, path, out_dir, interval): os.path.basename(path)
            for path, out_dir, interval in tasks
        }

        with tqdm(total=len(tasks), desc="处理进度", unit="视频") as pbar:
            for future in concurrent.futures.as_completed(future_to_video):
                video_name = future_to_video[future]
                try:
                    result = future.result()
                    tqdm.write(f"[完成] {video_name} -> {result}")
                except Exception as exc:
                    tqdm.write(f"[报错] {video_name} 产生了异常: {exc}")
                finally:
                    pbar.update(1)

if __name__ == "__main__":
    # 配置区（脚本在 src/ 下，项目根目录在上一层）
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    INPUT_DIR = os.path.join(BASE_DIR, "data", "input")
    OUTPUT_DIR = os.path.join(BASE_DIR, "data", "output")
    
    os.makedirs(INPUT_DIR, exist_ok=True)
    
    print(f"请确保视频已放入 '{INPUT_DIR}' 文件夹中...")
    
    # 执行并发批量处理
    # 可以手动指定 max_workers=4 (例如只用4个核心)，不填则火力全开
    batch_process_videos_concurrent(INPUT_DIR, OUTPUT_DIR, frame_interval=60)
    
    print("\n所有并发任务执行完毕！")