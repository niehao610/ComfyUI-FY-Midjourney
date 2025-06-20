import torch
from .api_client import MJClient
import asyncio


class MidjourneyImagineNode:
    def __init__(self):
        self.api_client = MJClient()

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "prompt": ("STRING", {"multiline": True, "default": "cat,cute,"}),
                "base_model": (["midjourney", "niji"], {"default": "midjourney"}),
                "version": (["5.0", "5.1", "5.2", "6", "6.1", "7.0"], {"default": "6"}),
            },
            "optional": {
                # "negative_prompt": ("STRING", {"multiline": True, "default": ""}),
                "image_ratio": (["1:1", "4:3", "3:4", "16:9", "9:16"], {"default": "1:1"}),
                "stylize": ("INT", {"default": 100, "min": 0, "max": 1000, "step": 1}),
                # "quality": ([".25", ".5", "1"], {"default": "1"}),
                "chaos": ("INT", {"default": 0, "min": 0, "max": 100, "step": 1}),
                "weird": ("INT", {"default": 0, "min": 0, "max": 3000, "step": 1}),
                "tile": ("BOOLEAN", {"default": False}),
                "q2": ("BOOLEAN", {"default": False}),
                "sref": ("STRING", {"default": ""}),
                # "no": ("STRING", {"default": ""}),
                "repeat": ("INT", {"default": 1, "min": 1, "max": 40, "step": 1}),
                "seed": ("INT", {"default": -1}),
                # "control_after_generate": (["fixed", "increment", "decrement", "randomize"], {"default": "fixed"}),
            }
        }

    RETURN_TYPES = ("IMAGE", "STRING", "DICT")
    RETURN_NAMES = ("image", "task_id", "buttons")
    FUNCTION = "generate"
    CATEGORY = "image"

    def generate(self, prompt, base_model, version, image_ratio="1:1",
                stylize=100, chaos=0, weird=0, tile=False, q2=False, sref="",
                repeat=1, seed=-1):
        try:
            # 构建完整提示词
            params = prompt
            params += f" --ar {image_ratio} --s {stylize} "
            if chaos > 0:
                params += f" --c {chaos}"
            if weird > 0:
                params += f" --weird {weird}"
            if tile:
                params += " --tile"
            if q2:
                params += " --q 2"
            if sref:
                params += f" --sref {sref}"
            if base_model != "niji":
                params += f" --v {version}"
            else:
                params += " --niji"
            if repeat > 1:
                params += f" --repeat {repeat}"
            if seed != -1:
                params += f" --seed {seed}"

            # 获取或创建事件循环
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

            # 异步执行 imagine
            imagine_task_id = loop.run_until_complete(
                self.api_client.imagine(text_prompt=params)
            )
            if not imagine_task_id:
                raise ValueError("Failed to get task_id from Midjourney API")

            # 异步等待结果
            image, task_id, buttons = loop.run_until_complete(
                self.api_client.sync_mj_status(task_id=imagine_task_id)
            )

            # 转换图像格式
            img_tensor = torch.from_numpy(image).float() / 255.0
            img_tensor = img_tensor.unsqueeze(0)  # Add batch dimension

            return (img_tensor, task_id, buttons)

        except Exception as e:
            print(f"Error in MidjourneyImagineNode: {str(e)}")
            raise