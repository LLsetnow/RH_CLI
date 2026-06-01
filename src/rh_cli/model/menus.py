from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ModelChoice:
    number: int
    name: str
    description: str
    text_endpoint: str
    image_endpoint: str
    aliases: tuple[str, ...]


IMAGE_CHOICES = (
    ModelChoice(1, "全能图片PRO", "香蕉Pro同款，默认推荐，综合效果最好", "rhart-image-n-pro/text-to-image", "rhart-image-n-pro/edit", ("全能", "pro", "效果最好", "默认")),
    ModelChoice(2, "全能图片V2", "香蕉2同款，最快最便宜", "rhart-image-n-g31-flash/text-to-image", "rhart-image-n-g31-flash/image-to-image", ("v2", "最快", "便宜")),
    ModelChoice(3, "悠船 v7", "Midjourney 风格，欧美大片质感", "youchuan/text-to-image-v7", "rhart-image-n-pro/edit", ("悠船", "midjourney", "mj")),
    ModelChoice(4, "GPT Image 2", "GPT image2 同款，语义理解强，改图也很稳", "rhart-image-g-2/text-to-image", "rhart-image-g-2/image-to-image", ("gpt", "g-2", "image 2")),
    ModelChoice(5, "Seedream v5", "字节跳动出品，写实照片感超强", "seedream-v5-lite/text-to-image", "seedream-v5-lite/image-to-image", ("seedream", "种子", "写实", "照片")),
)

VIDEO_CHOICES = (
    ModelChoice(1, "全能视频V3.1 Fast", "我最推荐的！又快效果又好，性价比之王", "rhart-video-v3.1-fast/text-to-video", "rhart-video-v3.1-fast/image-to-video", ("全能", "fast", "最快", "便宜", "默认")),
    ModelChoice(2, "全能视频X", "Grok 驱动，画面想象力超强，创意天花板", "rhart-video-g/text-to-video", "rhart-video-g/image-to-video", ("x", "grok", "创意")),
    ModelChoice(3, "可灵 v3.0 Pro", "运动特别自然，拍人物选它准没错", "kling-v3.0-pro/text-to-video", "kling-v3.0-pro/image-to-video", ("可灵", "kling", "人物")),
    ModelChoice(4, "全能视频V3.1 Pro", "电影感拉满，适合风景大片", "rhart-video-v3.1-pro/text-to-video", "rhart-video-v3.1-pro/image-to-video", ("pro", "电影", "风景")),
    ModelChoice(5, "Vidu Q3 Pro", "风格化独特，适合创意类短片", "vidu/text-to-video-q3-pro", "vidu/image-to-video-q3-pro", ("vidu", "q3")),
    ModelChoice(6, "全能视频S", "Sora 同款引擎效果好，但最近模型负载比较高，可能要多等一会儿", "rhart-video-s/text-to-video", "rhart-video-s/image-to-video", ("sora", "视频s")),
    ModelChoice(7, "海螺 Hailuo", "速度快画面细腻，适合创意类内容", "minimax/hailuo-02/t2v-pro", "minimax/hailuo-2.3-fast/image-to-video", ("海螺", "hailuo", "minimax")),
    ModelChoice(8, "Seedance 2.0", "效果超赞！最长15秒+自动配音+支持真人，最高4K，价格偏高", "rhart-video/sparkvideo-2.0/text-to-video", "rhart-video/sparkvideo-2.0/image-to-video", ("seedance", "种子", "15秒", "长视频", "4k", "自动配音")),
)


def find_choice(value: str | int | None, choices: tuple[ModelChoice, ...]) -> ModelChoice:
    if value is None or str(value).strip() == "":
        return choices[0]
    raw = str(value).strip()
    if raw.isdigit():
        number = int(raw)
        for choice in choices:
            if choice.number == number:
                return choice
    lowered = raw.lower()
    for choice in choices:
        if lowered in choice.name.lower() or any(lowered in alias.lower() for alias in choice.aliases):
            return choice
    return choices[0]


def endpoint_for_choice(choice: ModelChoice, *, has_input_image: bool) -> str:
    return choice.image_endpoint if has_input_image else choice.text_endpoint
