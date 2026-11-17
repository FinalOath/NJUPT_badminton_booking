"""输出任务安装参数；不注册任务、不连接服务器。"""
import json
import sys
import book
import scheduling

if __name__ == "__main__":
    try:
        print(json.dumps(scheduling.task_plan(book.load_config())))
    except (OSError, ValueError) as e:
        print(f"Invalid task configuration: {e}", file=sys.stderr)
        sys.exit(1)
