#!/usr/bin/env python3
"""
Python脚本：提取Verilog文件中always块内不在rst_n分支中赋值的信号
简化版本，只输出最终结果
"""

import re
import sys

def parse_verilog_file(file_path):
    """
    解析Verilog文件，提取always块和其中的信号赋值信息
    """
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # 移除单行注释
    content = re.sub(r'//.*', '', content)
    # 移除多行注释
    content = re.sub(r'/\*.*?\*/', '', content, flags=re.DOTALL)
    
    # 查找所有always块 - 使用更复杂的方法匹配嵌套的begin/end
    always_blocks = []
    pos = 0
    while True:
        # 查找always @开头
        always_match = re.search(r'always\s*@', content[pos:], re.IGNORECASE)
        if not always_match:
            break
        
        start_pos = pos + always_match.start()
        
        # 从always开始查找匹配的begin/end
        begin_count = 0
        end_count = 0
        i = start_pos
        block_start = -1
        
        while i < len(content):
            # 查找begin
            begin_match = re.match(r'begin', content[i:], re.IGNORECASE)
            if begin_match:
                if begin_count == 0:
                    block_start = i + begin_match.end()
                begin_count += 1
                i += begin_match.end()
                continue
            
            # 查找end
            end_match = re.match(r'end', content[i:], re.IGNORECASE)
            if end_match:
                end_count += 1
                i += end_match.end()
                if begin_count == end_count:
                    # 找到了匹配的end
                    block_end = i - end_match.end()
                    block = content[block_start:block_end]
                    always_blocks.append(block)
                    pos = i
                    break
                continue
            
            i += 1
        
        if begin_count != end_count:
            # 没有找到匹配的end，跳出循环
            break
    
    all_non_rst_n_signals = set()
    
    for i, block in enumerate(always_blocks, 1):
        # 查找rst_n分支中的赋值
        # 匹配 if(rst_n == 0) begin ... end 或 if(!rst_n) begin ... end
        rst_n_pattern = r'if\s*\(\s*(?:rst_n\s*==\s*0|!rst_n)\s*\)\s*begin(.*?)end'
        rst_n_matches = re.findall(rst_n_pattern, block, re.DOTALL | re.IGNORECASE)
        
        # 提取rst_n分支中赋值的信号
        rst_n_signals = set()
        for rst_n_block in rst_n_matches:
            # 查找非阻塞赋值 <=
            assignments = re.findall(r'(\w+)\s*<=', rst_n_block)
            rst_n_signals.update(assignments)
        
        # 提取整个always块中的所有赋值信号
        all_assignments = re.findall(r'(\w+)\s*<=', block)
        all_signals = set(all_assignments)
        
        # 找出不在rst_n分支中赋值的信号
        non_rst_n_signals = all_signals - rst_n_signals
        
        # 添加到总集合中
        all_non_rst_n_signals.update(non_rst_n_signals)
    
    return sorted(all_non_rst_n_signals)

def main():
    if len(sys.argv) != 2:
        print("用法: python find_signals_simple.py <verilog_file>")
        print("示例: python find_signals_simple.py test.v")
        sys.exit(1)
    
    file_path = sys.argv[1]
    
    try:
        signals = parse_verilog_file(file_path)
        
        # 输出结果
        if signals:
            print("不在rst_n分支中赋值的信号:")
            for signal in signals:
                print(f"  {signal}")
        else:
            print("没有找到不在rst_n分支中赋值的信号")
        
    except FileNotFoundError:
        print(f"错误: 文件 '{file_path}' 不存在")
        sys.exit(1)
    except Exception as e:
        print(f"错误: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()