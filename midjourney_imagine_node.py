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
                "app_key": ("STRING", {"default": "input your app key"}),   
            },
            "optional": {
                # "negative_prompt": ("STRING", {"multiline": True, "default": ""}),
                "image_ratio": (["1:1", "4:3", "3:4", "16:9", "9:16"], {"default": "1:1"}),
                "stylize": ("INT", {"default": 100, "min": 0, "max": 1000, "step": 1}),
                # "quality": ([".25", ".5", "1"], {"default": "1"}),
                "chaos": ("INT", {"default": 0, "min": 0, "max": 100, "step": 1}),
                "weird": ("INT", {"default": 0, "min": 0, "max": 3000, "step": 1}),
                #"tile": ("BOOLEAN", {"default": False}),
                #"q2": ("BOOLEAN", {"default": False}),
                "sref1": ("STRING", {"default": ""}),
                "sref2": ("STRING", {"default": ""}),
                "sw": ("INT", {"default": 30, "min": 0, "max": 100, "step": 1}),
                "oref": ("STRING", {"default": ""}),
                "ow": ("INT", {"default": 100, "min": 0, "max": 1000, "step": 10}), 
                           
                #"repeat": ("INT", {"default": 1, "min": 1, "max": 40, "step": 1}),
                #"seed": ("INT", {"default": -1}),
                #"control_after_generate": (["fixed", "increment", "decrement", "randomize"], {"default": "fixed"}),
            }
        }

    RETURN_TYPES = ("IMAGE", "STRING", "DICT")
    RETURN_NAMES = ("image", "task_id", "buttons")
    FUNCTION = "generate"
    CATEGORY = "image"

    def generate(self, prompt, app_key, image_ratio="1:1",
                stylize=100, chaos=0, weird=0, sref1="", sref2="", sw=30, oref="", ow=100):
        try:
            # 构建完整提示词
            params = prompt
            params += f" --ar {image_ratio} --s {stylize} "

            if chaos > 0:
                params += f" --c {chaos}"

            if weird > 0:
                params += f" --weird {weird}"

            if sref1 and len(sref1) > 1:
                params += f" --sref {sref1}"

                if sref2 and len(sref2) > 1:
                    params += f"  {sref2}"

                if sw > 0:
                    params += f" --sw {sw}"

            if oref and len(oref) > 1:
                params += f" --oref {oref}"
                if ow > 0:
                    params += f" --ow {ow}"

            params += f" --v 7.0"

            if len(app_key) < 3 or  app_key == "input your app key":
                raise ValueError("Invalid app key")

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