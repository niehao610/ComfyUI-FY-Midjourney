import os
import json
import numpy as np
from PIL import Image
from io import BytesIO
from .utils import init_logger, load_config
import asyncio
import aiohttp
import base64

logger = init_logger()
config = load_config()

logger.info(f"config: {config}")

class MJClient:
    def __init__(self):
        self.api_url = config['MIDJOURNEY_API']['api_url']
        self.api_key = config['MIDJOURNEY_API']['api_key']
        # 设置超时配置
        self.timeout = aiohttp.ClientTimeout(
            total=300,        # 总超时时间 5 分钟
            connect=30,       # 连接超时时间 30 秒
            sock_read=60      # 读取超时时间 60 秒
        )
        
        # 检测系统代理设置
        self.proxy_url = self._detect_system_proxy()
        if self.proxy_url:
            logger.info(f"检测到系统代理: {self.proxy_url}")
        else:
            logger.info("未检测到系统代理")

    def _detect_system_proxy(self):
        """
        检测系统代理设置
        """
        import os
        import urllib.request
        
        # 方法1: 检查环境变量
        proxy_vars = ['HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy']
        for var in proxy_vars:
            proxy = os.environ.get(var)
            if proxy:
                logger.debug(f"从环境变量 {var} 检测到代理: {proxy}")
                return proxy
        
        # 方法2: 检查系统代理设置（Windows）
        try:
            import winreg
            # 读取IE代理设置
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 
                               r"Software\Microsoft\Windows\CurrentVersion\Internet Settings") as key:
                proxy_enable, _ = winreg.QueryValueEx(key, "ProxyEnable")
                if proxy_enable:
                    proxy_server, _ = winreg.QueryValueEx(key, "ProxyServer")
                    if proxy_server:
                        # 如果包含协议，直接返回，否则添加http://
                        if not proxy_server.startswith(('http://', 'https://')):
                            proxy_server = f"http://{proxy_server}"
                        logger.debug(f"从Windows代理设置检测到: {proxy_server}")
                        return proxy_server
        except Exception as e:
            logger.debug(f"无法读取Windows代理设置: {e}")
        
        # 方法3: 常见的本地代理端口检测
        common_proxy_ports = [7890, 7891, 1080, 8080, 8888, 10809]
        for port in common_proxy_ports:
            proxy_url = f"http://127.0.0.1:{port}"
            if self._test_proxy_connection(proxy_url):
                logger.info(f"检测到可用的本地代理: {proxy_url}")
                return proxy_url
        
        return None

    def _test_proxy_connection(self, proxy_url, timeout=3):
        """
        测试代理连接是否可用
        """
        try:
            import socket
            from urllib.parse import urlparse
            
            parsed = urlparse(proxy_url)
            host = parsed.hostname
            port = parsed.port
            
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            result = sock.connect_ex((host, port))
            sock.close()
            
            return result == 0
        except Exception:
            return False

    async def imagine(self, text_prompt) -> str:
        """
        return task_id
        """
        logger.debug(f"Imagine with prompt: {text_prompt}")
        url = f"{self.api_url}/v1/api/trigger/imagine" 
        payload = json.dumps({
            "prompt": text_prompt,
            "picurl":""
        })
        headers = {
            'Authorization': 'Bearer {}'.format(self.api_key),
            'Content-Type': 'application/json; charset=utf-8'
        }
        try:
            async with aiohttp.ClientSession(timeout=self.timeout) as session:
                async with session.post(url, headers=headers, data=payload) as response:
                    response.raise_for_status()
                    # 首先尝试读取原始文本
                    text = await response.text()
                    logger.debug(f"Imagine response: {text}")
                    try:
                        # 尝试将文本解析为 JSON
                        result = json.loads(text)
                    except json.JSONDecodeError:
                        # 如果不是 JSON 格式，直接使用文本作为结果
                        logger.debug(f"Response is plain text: {text}")
                        result = {"result": text.strip()}
                    
                    logger.debug(f"Imagine response: {result}")
                    return result.get("result", None)
        except Exception as e:
            logger.error(f"Error during Imagine: {e}")
            raise

    async def _submit_upscale_vary_task(self, task_id, custom_id, session):
        """封装提交放大/变体任务的通用逻辑"""
        #custom_id = f"{action_type}||{index}||{msg_id}||{msg_hash}"

        vs = custom_id.split("||")
        action_type = vs[0]
        index = vs[1]
        msg_id = vs[2]
        msg_hash = vs[3]

        url = f"{self.api_url}/v1/api/trigger/upscale"
        if action_type == "upscale":
            url = f"{self.api_url}/v1/api/trigger/upscale"
        elif action_type == "vary":
            url = f"{self.api_url}/v1/api/trigger/vary"

        payload = json.dumps({
            "index": int(index),
            "msg_id": msg_id,
            "msg_hash": msg_hash,
            "trigger_id": task_id
        })
        
        headers = {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json; charset=utf-8'
        }
        
        async with session.post(url, headers=headers, data=payload) as response:
            response.raise_for_status()
            text = await response.text()
            try:
                result = json.loads(text)
            except json.JSONDecodeError:
                logger.debug(f"Response is plain text: {text}")
                result = {"result": text.strip()}
            
            return result.get("result", None)

    async def upscale_or_vary(self, task_id="", action="U1"):
        """
        执行单个放大或变体操作
        return: image
        """
        try:
            _, _, buttons = await self.sync_mj_status(task_id)
            
            # 添加调试信息
            logger.debug(f"Task_id: {task_id}, Buttons received: {buttons}")
            logger.debug(f"Task_id: {task_id}, Buttons type: {type(buttons)}")
            
            # 安全地获取 msg_id 和 msg_hash
            if isinstance(buttons, dict):
                msg_id = buttons.get('msg_id', 0)
                msg_hash = buttons.get('msg_hash', "")
            else:
                logger.error(f"Expected buttons to be dict, got {type(buttons)}: {buttons}")
                raise ValueError(f"Invalid buttons format: {type(buttons)}")
                
            index = int(action.replace("U", "").replace("V", ""))
            action_type = "upscale" if "U" in action else "vary"
            custom_id = f"{action_type}||{index}||{msg_id}||{msg_hash}"
            
            logger.debug(f"Generated custom_id: {custom_id}")
            
            async with aiohttp.ClientSession(timeout=self.timeout) as session:
                subtask_id = await self._submit_upscale_vary_task(task_id, custom_id, session)
                if not subtask_id:
                    raise ValueError("Failed to get subtask_id")
                
                image, _, _ = await self.sync_mj_status(task_id=subtask_id)
                return image
                
        except Exception as e:
            logger.error(f"Error during Upscale/Vary: {e}")
            raise

    async def sync_mj_status(self, task_id):
        """
        异步轮询任务状态
        return image, task_id, buttons 
        """
        try:
            async with aiohttp.ClientSession(timeout=self.timeout) as session:
                while True:
                    url = f"{self.api_url}/v1/api/trigger/task/{task_id}"
                    headers = {
                        'Authorization': f'Bearer {self.api_key}',
                        'Content-Type': 'application/json; charset=utf-8'
                    }
                    
                    async with session.get(url, headers=headers) as response:
                        response.raise_for_status()
                        # 首先读取原始文本
                        text = await response.text()
                        logger.debug(f"Response is plain text: {text}")
                        try:
                            # 尝试将文本解析为 JSON
                            data = json.loads(text)
                        except json.JSONDecodeError:
                            logger.debug(f"Response is plain text: {text}")
                            raise ValueError(f"Expected JSON response but got: {text}")
                        
                        logger.debug(f"Fetch response: {data}")
                        
                        status = data['status']
                        buttons = {"msg_id": 0, "msg_hash": ""}

                        if status == 'SUCCESS':
                            img = None
                            if 'imageUrl' in data:
                                img = await self.download_image_ultimate(data['imageUrl'])
                            if 'buttons' in data:
                                # 确保 buttons 包含正确的 msg_id 和 msg_hash
                                raw_buttons = data['buttons']
                                if isinstance(raw_buttons, dict):
                                    # 如果已经是字典，检查是否包含必要的字段
                                    if 'msg_id' in raw_buttons and 'msg_hash' in raw_buttons:
                                        buttons = raw_buttons
                                    else:
                                        # 如果是旧格式的按钮，需要从 data 中获取 msg_id 和 msg_hash
                                        buttons = {
                                            "msg_id": data.get('msg_id', 0),
                                            "msg_hash": data.get('msg_hash', ""),
                                            "buttons": raw_buttons  # 保留原始按钮信息
                                        }
                                elif isinstance(raw_buttons, list):
                                    # 如果是按钮列表，提取 msg_id 和 msg_hash
                                    buttons = {
                                        "msg_id": data.get('msg_id', 0),
                                        "msg_hash": data.get('msg_hash', ""),
                                        "buttons": raw_buttons
                                    }
                                else:
                                    logger.warning(f"Unexpected buttons format: {type(raw_buttons)}, value: {raw_buttons}")
                                    buttons = {
                                        "msg_id": data.get('msg_id', 0),
                                        "msg_hash": data.get('msg_hash', "")
                                    }
                            else:
                                # 如果没有 buttons 字段，从 data 中获取
                                buttons = {
                                    "msg_id": data.get('msg_id', 0),
                                    "msg_hash": data.get('msg_hash', "")
                                }
                            return img, task_id, buttons
                        
                        elif status in ['FAILED', 'FAILURE']:
                            # 统一处理失败与超时 (FAILURE) 状态，将具体 failReason 抛出供上层 (ComfyUI) 捕获
                            raise Exception(f"Task failed: {data.get('failReason', 'Unknown error')}")
                        
                        elif status in ['', 'SUBMITTED', 'IN_PROGRESS', 'NOT_START']:
                            logger.info(f"Task status: {status}, progress: {data.get('progress', 'Unknown')}")
                            await asyncio.sleep(5)  # 异步等待3秒
                        
                        else:
                            raise Exception(f"Unknown task status: {data['status']}")
                        
        except Exception as e:
            logger.error(f"Error during sync_mj_status: {e}")
            raise

    async def download_image(self, url, max_retries=3):
        """异步下载图片并转换为numpy数组，支持重试和浏览器模拟"""
        logger.debug(f"Downloading image from URL: {url}")
        
        # 为图片下载创建专门的超时配置
        download_timeout = aiohttp.ClientTimeout(
            total=120,      # 图片下载总超时 2 分钟
            connect=20,     # 连接超时 20 秒
            sock_read=30    # 读取超时 30 秒
        )
        
        # 模拟浏览器请求头
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'image',
            'Sec-Fetch-Mode': 'no-cors',
            'Sec-Fetch-Site': 'cross-site',
        }
        
        for attempt in range(max_retries):
            try:
                # 使用 SSL 验证跳过（有些情况下 Discord CDN 可能有证书问题）
                connector = aiohttp.TCPConnector(ssl=False)
                async with aiohttp.ClientSession(
                    timeout=download_timeout, 
                    connector=connector,
                    headers=headers
                ) as session:
                    logger.debug(f"Attempt {attempt + 1}/{max_retries} to download image")
                    async with session.get(url) as response:
                        response.raise_for_status()
                        image_data = await response.read()
                        img = Image.open(BytesIO(image_data))
                        logger.debug(f"Successfully downloaded image, size: {len(image_data)} bytes")
                        return np.array(img)
                        
            except Exception as e:
                logger.warning(f"Attempt {attempt + 1}/{max_retries} failed: {str(e)}")
                if attempt == max_retries - 1:  # 最后一次尝试
                    logger.error(f"Failed to download image after {max_retries} attempts from URL {url}: {str(e)}")
                    raise
                else:
                    # 等待后重试，使用指数退避
                    wait_time = 2 ** attempt
                    logger.debug(f"Waiting {wait_time} seconds before retry...")
                    await asyncio.sleep(wait_time)

    async def download_image_fallback(self, url):
        """备用图片下载方法，使用更宽松的SSL配置"""
        logger.debug(f"Using fallback method to download image from URL: {url}")
        try:
            import ssl
            # 创建不验证SSL证书的上下文
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
            
            # 为图片下载创建专门的超时配置
            download_timeout = aiohttp.ClientTimeout(
                total=180,      # 图片下载总超时 3 分钟
                connect=30,     # 连接超时 30 秒
                sock_read=60    # 读取超时 60 秒
            )
            
            # 模拟浏览器请求头
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8',
                'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
                'Accept-Encoding': 'gzip, deflate, br',
                'Connection': 'keep-alive',
            }
            
            connector = aiohttp.TCPConnector(ssl=ssl_context)
            async with aiohttp.ClientSession(
                timeout=download_timeout, 
                connector=connector
            ) as session:
                async with session.get(url, headers=headers) as response:
                    response.raise_for_status()
                    image_data = await response.read()
                    img = Image.open(BytesIO(image_data))
                    logger.debug(f"Successfully downloaded image using fallback method, size: {len(image_data)} bytes")
                    return np.array(img)
                    
        except Exception as e:
            logger.error(f"Fallback download also failed for URL {url}: {str(e)}")
            raise

    def image_to_base64(self, image_path):
        """
        将图片文件转换为 base64 格式

        Args:
            image_path (str): 图片文件路径

        Returns:
            str: base64 编码的图片数据，格式如 "data:image/png;base64,xxx"
        """
        try:
            with open(image_path, "rb") as image_file:
                # 读取图片数据
                image_data = image_file.read()
                # 转换为 base64
                base64_data = base64.b64encode(image_data).decode('utf-8')

                # 根据文件扩展名确定 MIME 类型
                file_extension = os.path.splitext(image_path)[1].lower()
                if file_extension in ['.jpg', '.jpeg']:
                    mime_type = 'image/jpeg'
                elif file_extension == '.png':
                    mime_type = 'image/png'
                elif file_extension == '.gif':
                    mime_type = 'image/gif'
                elif file_extension == '.webp':
                    mime_type = 'image/webp'
                else:
                    # 默认使用 png
                    mime_type = 'image/png'

                return f"data:{mime_type};base64,{base64_data}"
        except Exception as e:
            logger.error(f"Error converting image to base64: {e}")
            raise

    async def blend(self, base64_images, dimensions="SQUARE", bot_type="MID_JOURNEY", quality=None, notify_hook="", state=""):
        """
        提交 Blend 任务（图片混合）

        Args:
            base64_images (list): 图片base64数组，格式如 ["data:image/png;base64,xxx1", "data:image/png;base64,xxx2"]
            dimensions (str): 图片比例，可选值: "PORTRAIT"(2:3), "SQUARE"(1:1), "LANDSCAPE"(3:2)
            bot_type (str): bot类型，可选值: "MID_JOURNEY", "NIJI_JOURNEY"
            quality (str): 图像质量，可选值: "hd"
            notify_hook (str): 回调地址，为空时使用全局notifyHook
            state (str): 自定义参数

        Returns:
            str: task_id
        """
        logger.debug(f"Blend with {len(base64_images)} images, dimensions: {dimensions}")
        url = f"{self.api_url}/mj/submit/blend"

        payload_data = {
            "botType": bot_type,
            "base64Array": base64_images,
            "dimensions": dimensions,
            "notifyHook": notify_hook,
            "state": state
        }

        # 只有当 quality 不为 None 时才添加到 payload 中
        if quality is not None:
            payload_data["quality"] = quality

        payload = json.dumps(payload_data)

        headers = {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json; charset=utf-8'
        }

        try:
            async with aiohttp.ClientSession(timeout=self.timeout) as session:
                async with session.post(url, headers=headers, data=payload) as response:
                    response.raise_for_status()
                    # 首先尝试读取原始文本
                    text = await response.text()
                    try:
                        # 尝试将文本解析为 JSON
                        result = json.loads(text)
                    except json.JSONDecodeError:
                        # 如果不是 JSON 格式，直接使用文本作为结果
                        logger.debug(f"Response is plain text: {text}")
                        result = {"result": text.strip()}

                    logger.debug(f"Blend response: {result}")
                    return result.get("result", None)
        except Exception as e:
            logger.error(f"Error during Blend: {e}")
            raise


    async def batch_upscale_or_vary(self, task_id, actions=["U1", "U2", "U3", "U4"]):
        """
        批量处理多个放大或变体任务
        return: List[Image]
        """
        try:
            _, _, buttons = await self.sync_mj_status(task_id)

            async def submit_task(action):
                try:
                    custom_id = buttons[action]
                    subtask_id = await self._submit_upscale_vary_task(task_id, custom_id, session)
                    if subtask_id:
                        logger.debug(f"Submitted {action} task: {subtask_id}")
                        return action, subtask_id
                except Exception as e:
                    logger.error(f"Error submitting {action} task: {e}")
                    return None

            async with aiohttp.ClientSession(timeout=self.timeout) as session:
                tasks = [submit_task(action) for action in actions]
                results = await asyncio.gather(*tasks)
                subtask_ids = [r for r in results if r is not None]

            tasks = [self.sync_mj_status(subtask_id) for _, subtask_id in subtask_ids]
            results = []
            completed_tasks = await asyncio.gather(*tasks, return_exceptions=True)

            for (action, subtask_id), task_result in zip(subtask_ids, completed_tasks):
                if isinstance(task_result, Exception):
                    logger.error(f"Error processing {action} task {subtask_id}: {task_result}")
                    continue
                image, _, _ = task_result
                results.append(image)
                logger.debug(f"Completed {action} task: {subtask_id}")

            return results
        except Exception as e:
            logger.error(f"Error during batch upscale/vary: {e}")
            raise

    async def network_diagnostic(self, url):
        """
        网络诊断功能
        """
        import socket
        from urllib.parse import urlparse
        
        print("=== 网络诊断开始 ===")
        
        # 解析URL
        parsed_url = urlparse(url)
        host = parsed_url.hostname
        port = parsed_url.port or (443 if parsed_url.scheme == 'https' else 80)
        
        print(f"目标地址: {host}:{port}")
        
        # 1. DNS解析测试
        try:
            import socket
            print("1. DNS解析测试...")
            ip_address = socket.gethostbyname(host)
            print(f"   ✓ DNS解析成功: {host} -> {ip_address}")
        except Exception as e:
            print(f"   ✗ DNS解析失败: {e}")
            return False
        
        # 2. TCP连接测试
        try:
            print("2. TCP连接测试...")
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(10)
            result = sock.connect_ex((host, port))
            sock.close()
            if result == 0:
                print(f"   ✓ TCP连接成功")
            else:
                print(f"   ✗ TCP连接失败: 错误代码 {result}")
                return False
        except Exception as e:
            print(f"   ✗ TCP连接测试失败: {e}")
            return False
        
        # 3. HTTP请求测试
        try:
            print("3. HTTP请求测试...")
            timeout = aiohttp.ClientTimeout(total=30, connect=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.head(url) as response:
                    print(f"   ✓ HTTP请求成功: 状态码 {response.status}")
        except Exception as e:
            print(f"   ✗ HTTP请求失败: {e}")
            return False
        
        print("=== 网络诊断完成 - 网络连接正常 ===")
        return True

    async def download_image_with_proxy(self, url, proxy_url=None):
        """
        使用代理下载图片
        """
        logger.debug(f"Downloading image with proxy from URL: {url}")
        
        download_timeout = aiohttp.ClientTimeout(
            total=300,      # 5分钟总超时
            connect=60,     # 1分钟连接超时
            sock_read=120   # 2分钟读取超时
        )
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Cache-Control': 'no-cache',
            'Pragma': 'no-cache',
        }
        
        try:
            import ssl
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
            
            connector_kwargs = {'ssl': ssl_context}
            if proxy_url:
                connector_kwargs['limit'] = 100
                
            connector = aiohttp.TCPConnector(**connector_kwargs)
            
            session_kwargs = {
                'timeout': download_timeout,
                'connector': connector,
                'headers': headers
            }
            
            async with aiohttp.ClientSession(**session_kwargs) as session:
                get_kwargs = {}
                if proxy_url:
                    get_kwargs['proxy'] = proxy_url
                    
                async with session.get(url, **get_kwargs) as response:
                    response.raise_for_status()
                    image_data = await response.read()
                    img = Image.open(BytesIO(image_data))
                    logger.debug(f"Successfully downloaded image via proxy, size: {len(image_data)} bytes")
                    return np.array(img)
                    
        except Exception as e:
            logger.error(f"Proxy download failed for URL {url}: {str(e)}")
            raise

    async def download_image_ultimate(self, url, max_retries=3):
        """
        终极图片下载方法，尝试多种策略
        """
        logger.debug(f"Ultimate download attempt for URL: {url}")
        
        strategies = []
        
        # 如果检测到代理，优先使用代理下载
        if self.proxy_url:
            strategies.append(("代理下载", lambda: self.download_image_with_proxy(url, self.proxy_url)))
        
        # 添加其他下载策略
        strategies.extend([
            ("标准下载", lambda: self.download_image(url, max_retries)),
            ("备用下载", lambda: self.download_image_fallback(url)),
        ])
        
        # 如果没有自动检测到代理，尝试常见代理端口
        if not self.proxy_url:
            common_proxies = [
                "http://127.0.0.1:33210",   # Clash
            ]
            for proxy in common_proxies:
                strategies.append((f"代理下载({proxy})", lambda p=proxy: self.download_image_with_proxy(url, p)))
        
        last_error = None
        for strategy_name, strategy_func in strategies:
            try:
                logger.info(f"尝试 {strategy_name}...")
                result = await strategy_func()
                logger.info(f"{strategy_name} 成功！")
                return result
            except Exception as e:
                logger.warning(f"{strategy_name} 失败: {e}")
                last_error = e
                await asyncio.sleep(1)  # 策略间等待
        
        # 所有策略都失败
        raise Exception(f"所有下载策略都失败，最后错误: {last_error}")

if __name__ == "__main__":
    import asyncio
    
    async def test_connection():
        print("=== ComfyUI-MidjourneyHub 网络连接测试 ===")
        client = MJClient()
        test_url = "https://cdn.discordapp.com/attachments/1384158875657175166/1385555092073218170/forrynie.1981_1130645665boycute_a706f469-ed90-409b-9a4a-4e9c566711f4.png?ex=68567e3c&is=68552cbc&hm=bd72f6f9f0de355b19dbfff6b3369a7132a4c9762cdce97562ddffe1c58e491e&"
        
        print(f"\n检测到的代理设置: {client.proxy_url or '无'}")
        
        # 网络诊断
        print("\n=== 开始网络诊断 ===")
        diagnostic_result = await client.network_diagnostic(test_url)
        
        if not diagnostic_result:
            print("\n⚠️  网络诊断失败，这可能表明需要使用代理")
            print("常见的代理软件端口:")
            print("  - Clash: 7890, 7891")
            print("  - V2Ray: 10809")
            print("  - 其他: 1080, 8080, 8888")
            print("\n如果您正在使用代理软件，请确保它正在运行")
        
        # 测试图片下载
        print("\n=== 开始图片下载测试 ===")
        try:
            image = await client.download_image_ultimate(test_url)
            print(f"✅ 下载成功！图片大小: {image.shape}")
        except Exception as e:
            print(f"❌ 下载失败: {e}")
            print("\n💡 建议:")
            print("1. 检查您的网络连接")
            print("2. 如果使用代理，确保代理软件正在运行")
            print("3. 尝试在浏览器中访问测试链接")
            print("4. 检查防火墙设置")
    
    # 运行异步测试
    asyncio.run(test_connection())