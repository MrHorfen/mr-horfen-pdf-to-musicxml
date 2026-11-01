"""
文件: omr_engine_batch.py
用途: 基于Audiveris引擎批量识别乐谱图像中的音乐元素，支持单声部/钢琴大谱表模式，捕获识别置信度数据
调用方: score_converter 主流程
输入: 预处理后的乐谱图像（PNG/PDF）
输出: MusicXML格式的识别结果文件
依赖: subprocess, Audiveris CLI
作者: Meandro(菠萝的自我救赎）
创建时间: 2026-07-31
最后更新: 2026-07-31
更新说明: 补充标准化头部注释
"""

import os
import re
import json
import logging
import subprocess
import tempfile
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field
import xml.etree.ElementTree as ET

logger = logging.getLogger(__name__)


@dataclass
class OMRConfidence:
    """OMR识别置信度数据"""
    element_type: str
    element_id: str
    confidence: float
    bbox: Optional[Tuple[int, int, int, int]] = None
    page: int = 1


@dataclass
class OMRResult:
    """OMR识别结果"""
    musicxml_path: Optional[str] = None
    confidence_data: List[OMRConfidence] = field(default_factory=list)
    raw_output_dir: Optional[str] = None
    success: bool = False
    error_message: Optional[str] = None


class OMREngine:
    """
    OMR识别引擎封装类，基于Audiveris命令行调用
    """

    def __init__(
        self,
        audiveris_path: str = "audiveris",
        java_path: str = "java",
        omr_mode: str = "printed",
        sheet_type: str = "single",
        confidence_threshold: float = 0.8,
        timeout_seconds: int = 300,
    ):
        """
        初始化OMR引擎

        Args:
            audiveris_path: Audiveris可执行文件路径
            java_path: Java可执行文件路径
            omr_mode: OMR模式 (printed|handwritten)
            sheet_type: 谱面类型 (single|piano)
            confidence_threshold: 置信度阈值
            timeout_seconds: 单次识别超时时间
        """
        self.audiveris_path = audiveris_path
        self.java_path = java_path
        self.omr_mode = omr_mode
        self.sheet_type = sheet_type
        self.confidence_threshold = confidence_threshold
        self.timeout_seconds = timeout_seconds

        self._validate_environment()

    def _validate_environment(self):
        """
        验证运行环境
        """
        try:
            result = subprocess.run(
                [self.java_path, "-version"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            logger.info("Java环境验证通过")
        except Exception as e:
            logger.warning(f"Java环境检测失败: {e}")

    def recognize(
        self,
        image_path: str,
        output_dir: str,
    ) -> OMRResult:
        """
        对单张图像进行OMR识别

        Args:
            image_path: 输入图像路径
            output_dir: 输出目录

        Returns:
            OMR识别结果
        """
        image_path = Path(image_path)
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        result = OMRResult()

        try:
            audiveris_output = output_dir / image_path.stem
            audiveris_output.mkdir(exist_ok=True)

            cmd = self._build_command(str(image_path), str(audiveris_output))
            logger.info(f"执行Audiveris命令: {' '.join(cmd)}")

            process = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                encoding="utf-8",
                errors="ignore",
            )

            logger.debug(f"Audiveris stdout: {process.stdout}")
            if process.stderr:
                logger.debug(f"Audiveris stderr: {process.stderr}")

            if process.returncode != 0:
                result.error_message = f"Audiveris执行失败 (code={process.returncode}): {process.stderr}"
                logger.error(result.error_message)
                return result

            musicxml_path = self._find_musicxml(str(audiveris_output))
            if musicxml_path:
                result.musicxml_path = musicxml_path
                result.success = True
                logger.info(f"MusicXML生成成功: {musicxml_path}")
            else:
                result.error_message = "未找到生成的MusicXML文件"
                logger.error(result.error_message)
                return result

            result.confidence_data = self._extract_confidence(
                str(audiveris_output)
            )
            result.raw_output_dir = str(audiveris_output)

        except subprocess.TimeoutExpired:
            result.error_message = f"Audiveris识别超时 ({self.timeout_seconds}s)"
            logger.error(result.error_message)
        except Exception as e:
            result.error_message = f"OMR识别异常: {e}"
            logger.error(result.error_message)

        return result

    def recognize_batch(
        self,
        image_paths: List[str],
        output_dir: str,
    ) -> Dict[str, OMRResult]:
        """
        批量识别图像

        Args:
            image_paths: 图像路径列表
            output_dir: 输出目录

        Returns:
            字典，键为图像名，值为识别结果
        """
        results = {}
        for img_path in image_paths:
            try:
                img_name = Path(img_path).stem
                img_output_dir = Path(output_dir) / img_name
                result = self.recognize(img_path, str(img_output_dir))
                results[img_name] = result
            except Exception as e:
                logger.error(f"批量识别失败 [{img_path}]: {e}")
                results[Path(img_path).stem] = OMRResult(
                    error_message=str(e)
                )

        return results

    def _build_command(
        self, image_path: str, output_dir: str
    ) -> List[str]:
        """
        构建Audiveris命令行参数
        """
        cmd = [
            self.audiveris_path,
            "-batch",
            "-export",
            "-output", output_dir,
            image_path,
        ]

        if self.sheet_type == "piano":
            cmd.extend(["-option", "org.audiveris.omr.sheet.BookManager.twoStaves=true"])

        return cmd

    def _find_musicxml(self, output_dir: str) -> Optional[str]:
        """
        在输出目录中查找生成的MusicXML文件
        """
        output_path = Path(output_dir)
        for pattern in ["*.xml", "*.musicxml", "*.mxl"]:
            files = list(output_path.rglob(pattern))
            if files:
                return str(files[0])
        return None

    def _extract_confidence(self, output_dir: str) -> List[OMRConfidence]:
        """
        从Audiveris输出中提取置信度数据
        """
        confidence_data = []
        output_path = Path(output_dir)

        omr_file = output_path / "omr.xml"
        if not omr_file.exists():
            omr_file = output_path / f"{output_path.name}.omr"

        if omr_file.exists():
            try:
                tree = ET.parse(str(omr_file))
                root = tree.getroot()

                for elem in root.iter():
                    if "confidence" in elem.attrib:
                        conf = OMRConfidence(
                            element_type=elem.tag,
                            element_id=elem.get("id", "unknown"),
                            confidence=float(elem.get("confidence", 0)),
                            page=int(elem.get("page", 1)),
                        )
                        confidence_data.append(conf)
            except Exception as e:
                logger.warning(f"解析置信度数据失败: {e}")

        if not confidence_data:
            logger.info("未找到结构化置信度数据，尝试从日志解析")
            confidence_data = self._parse_confidence_from_logs(output_dir)

        return confidence_data

    def _parse_confidence_from_logs(
        self, output_dir: str
    ) -> List[OMRConfidence]:
        """
        从Audiveris日志中解析置信度信息
        """
        confidence_data = []
        log_file = Path(output_dir) / "audiveris.log"

        if not log_file.exists():
            return confidence_data

        try:
            with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    match = re.search(
                        r"confidence[:\s]+(\d+\.?\d*)", line, re.IGNORECASE
                    )
                    if match:
                        conf = OMRConfidence(
                            element_type="unknown",
                            element_id="log",
                            confidence=float(match.group(1)),
                        )
                        confidence_data.append(conf)
        except Exception as e:
            logger.warning(f"解析日志置信度失败: {e}")

        return confidence_data


def run_omr(
    image_path: str,
    output_dir: str,
    audiveris_path: str = "audiveris",
    sheet_type: str = "single",
    timeout: int = 300,
) -> OMRResult:
    """
    便捷的OMR识别函数

    Args:
        image_path: 输入图像路径
        output_dir: 输出目录
        audiveris_path: Audiveris路径
        sheet_type: 谱面类型
        timeout: 超时时间

    Returns:
        OMR识别结果
    """
    engine = OMREngine(
        audiveris_path=audiveris_path,
        sheet_type=sheet_type,
        timeout_seconds=timeout,
    )
    return engine.recognize(image_path, output_dir)
