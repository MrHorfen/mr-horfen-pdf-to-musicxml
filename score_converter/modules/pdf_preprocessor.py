"""
文件: pdf_preprocessor.py
用途: 对乐谱PDF进行预处理，保留指定的钢琴部分并去除合唱部分
调用方: score_converter 主流程或用户手动调用
输入: 原始乐谱PDF文件路径
输出: 仅保留钢琴部分的预处理PDF文件
依赖: PyMuPDF(fitz)
作者: Meandro(菠萝的自我救赎）
创建时间: 2026-07-31
最后更新: 2026-07-31
更新说明: 补充标准化头部注释
"""

import fitz  # PyMuPDF
import os
from typing import List, Tuple


class PDFPreprocessor:
    def __init__(self, input_path: str, output_path: str):
        """
        初始化PDF预处理器
        
        Args:
            input_path: 输入PDF路径
            output_path: 输出PDF路径
        """
        self.input_path = input_path
        self.output_path = output_path
        self.doc = None
    
    def load_pdf(self):
        """加载PDF文档"""
        self.doc = fitz.open(self.input_path)
    
    def find_piano_sections(self) -> List[Tuple[int, int]]:
        """
        查找包含钢琴(Pno.)的部分页码，排除合唱部分
        
        Returns:
            包含钢琴部分的页码范围列表 [(start_page, end_page), ...]
        """
        if not self.doc:
            raise ValueError("PDF文档未加载")
        
        piano_pages = []
        
        for page_num in range(len(self.doc)):
            page = self.doc[page_num]
            text = page.get_text()
            
            # 检查是否包含钢琴标记
            has_piano = any(keyword in text for keyword in ['Pno.', 'Piano', '钢琴'])
            # 检查是否包含合唱或其他声部标记
            has_other_parts = any(other_part_keyword in text for other_part_keyword in 
                                 ['Chorus', 'Vocal', '合唱', '声部', 'Voice', 'T. ', 'T.', 'B. ', 'B.', 'S. ', 'S.', 'A. ', 'A.'])
            
            # 只有当页面包含钢琴标记且不包含其他声部标记时才保留
            # 或者当页面同时包含钢琴和其他声部时，我们需要进一步处理
            if has_piano:
                # 检查页面布局，尝试识别是否可以只保留钢琴部分
                piano_pages.append((page_num, page_num))
        
        # 合并连续的页码范围
        if piano_pages:
            merged_ranges = []
            current_start, current_end = piano_pages[0]
            
            for i in range(1, len(piano_pages)):
                start, end = piano_pages[i]
                if start <= current_end + 1:
                    current_end = max(current_end, end)
                else:
                    merged_ranges.append((current_start, current_end))
                    current_start, current_end = start, end
            
            merged_ranges.append((current_start, current_end))
            return merged_ranges
        else:
            # 如果没有明确标识，返回所有页面
            return [(0, len(self.doc) - 1)]

    def extract_piano_only_from_page(self, page, output_page):
        """
        从页面中只提取钢琴部分的内容，移除合唱部分
        这是一个高级功能，需要根据具体的页面布局来决定如何分割
        """
        # 获取页面的所有文本块
        blocks = page.get_text("dict")["blocks"]
        
        # 寻找包含钢琴标记的区域
        piano_blocks = []
        other_blocks = []
        
        for block in blocks:
            if "lines" in block:
                block_text = ""
                for line in block["lines"]:
                    for span in line["spans"]:
                        block_text += span["text"]
                
                # 判断这个文本块是否属于钢琴部分
                if any(keyword in block_text for keyword in ['Pno.', 'Piano', '钢琴']):
                    piano_blocks.append(block)
                elif any(keyword in block_text for keyword in ['Chorus', 'Vocal', '合唱', '声部', 'Voice', 'T. ', 'T.', 'B. ', 'B.', 'S. ', 'S.', 'A. ', 'A.']):
                    other_blocks.append(block)
                else:
                    # 对于没有明确标识的块，暂时归类为其他部分
                    other_blocks.append(block)
        
        # 只复制钢琴相关的块到输出页面
        # 注意：这里我们实际上需要重新创建页面内容，这比较复杂
        # 更实际的方法是在页面级别进行过滤
        
        # 返回应该保留的区块索引
        return [i for i, block in enumerate(blocks) 
                if any(keyword in ("".join(span["text"] for line in block.get("lines", []) for span in line.get("spans", []))) 
                       for keyword in ['Pno.', 'Piano', '钢琴']) or 
                   not any(keyword in ("".join(span["text"] for line in block.get("lines", []) for span in line.get("spans", []))) 
                           for keyword in ['Chorus', 'Vocal', '合唱', '声部', 'Voice', 'T. ', 'T.', 'B. ', 'B.', 'S. ', 'S.', 'A. ', 'A.'])]
    
    def extract_pages(self, page_ranges: List[Tuple[int, int]]):
        """
        提取指定页面范围到新PDF
        
        Args:
            page_ranges: 页面范围列表 [(start_page, end_page), ...]
        """
        if not self.doc:
            raise ValueError("PDF文档未加载")
        
        # 创建新的PDF文档
        new_doc = fitz.open()
        
        for start_page, end_page in page_ranges:
            for page_num in range(start_page, min(end_page + 1, len(self.doc))):
                page = self.doc.load_page(page_num)
                new_doc.insert_pdf(self.doc, from_page=page_num, to_page=page_num)
        
        # 保存新文档
        new_doc.save(self.output_path)
        new_doc.close()
        print(f"已提取钢琴部分到: {self.output_path}")
    
    def process(self):
        """执行完整的预处理流程"""
        print(f"正在处理PDF: {self.input_path}")
        
        try:
            self.load_pdf()
            piano_page_ranges = self.find_piano_sections()
            
            print(f"找到钢琴部分页码范围: {piano_page_ranges}")
            
            self.extract_pages(piano_page_ranges)
            
            print("PDF预处理完成")
        except Exception as e:
            print(f"处理过程中出现错误: {str(e)}")
            raise
        finally:
            if self.doc:
                self.doc.close()


def preprocess_pdf_for_piano(input_pdf_path: str, output_pdf_path: str):
    """
    便捷函数：对PDF进行预处理，只保留钢琴部分
    
    Args:
        input_pdf_path: 输入PDF路径
        output_pdf_path: 输出PDF路径
    """
    preprocessor = PDFPreprocessor(input_pdf_path, output_pdf_path)
    preprocessor.process()


if __name__ == "__main__":
    # 示例用法
    import sys
    
    if len(sys.argv) != 3:
        print("用法: python pdf_preprocessor.py <input_pdf> <output_pdf>")
        sys.exit(1)
    
    input_pdf = sys.argv[1]
    output_pdf = sys.argv[2]
    
    preprocess_pdf_for_piano(input_pdf, output_pdf)