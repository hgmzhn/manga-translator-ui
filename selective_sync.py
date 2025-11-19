# -*- coding: utf-8 -*-
import subprocess
import sys
import os

# 开启Windows ANSI颜色支持
os.system('')

# 配置颜色
class Colors:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'

def print_color(text, color=Colors.ENDC):
    print(f"{color}{text}{Colors.ENDC}")

def run_cmd(cmd, check=True, capture=True):
    try:
        if capture:
            result = subprocess.run(cmd, shell=True, check=check, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8')
            return result.stdout.strip()
        else:
            result = subprocess.run(cmd, shell=True, check=check)
            return None
    except subprocess.CalledProcessError as e:
        if check:
            print_color(f"\n[错误] 执行命令失败: {cmd}", Colors.RED)
            if capture:
                print_color(e.stderr, Colors.RED)
            sys.exit(1)
        return None

def get_commits(upstream="origin", branch="main"):
    # 获取当前分支
    current = run_cmd("git branch --show-current")
    
    # 显示检查信息
    print_color(f"\n📍 当前分支: {current}", Colors.CYAN)
    print_color(f"🔍 检查目标: {upstream}/{branch}", Colors.CYAN)
    
    # Fetch
    print_color(f"\n正在获取远程更新...", Colors.CYAN)
    run_cmd(f"git fetch {upstream}", check=False, capture=False)
    
    # 获取当前分支最新提交
    current_hash = run_cmd("git rev-parse --short HEAD", check=False)
    current_msg = run_cmd('git log -1 --pretty=format:"%s"', check=False)
    print_color(f"✅ 当前版本: {current_hash} - {current_msg}", Colors.GREEN)
    
    # 获取差异提交
    # 格式: hash|author|date|message
    log_cmd = f'git log {current}..{upstream}/{branch} --pretty=format:"%h|%an|%ar|%s"'
    output = run_cmd(log_cmd, check=False)
    
    commits = []
    if output:
        for line in output.splitlines():
            parts = line.split('|', 3)
            if len(parts) == 4:
                commits.append({
                    'hash': parts[0],
                    'author': parts[1],
                    'date': parts[2],
                    'msg': parts[3]
                })
    return commits, current

def resolve_conflict():
    print_color("\n⚠️ 检测到合并冲突！", Colors.RED)
    print_color("请在另一个终端手动解决冲突：", Colors.YELLOW)
    print("1. 编辑冲突文件解决冲突")
    print("2. git add .")
    print("3. git cherry-pick --continue")
    print_color("\n或者选择放弃此提交：", Colors.YELLOW)
    print("git cherry-pick --abort")
    
    while True:
        choice = input("\n冲突解决了吗？(y=已解决继续 / a=放弃此提交): ").lower().strip()
        if choice == 'y':
            try:
                # 尝试继续，如果用户已经commit了可能会报错，如果是add了会提交
                # 先检查是否正在cherry-pick
                if os.path.exists(".git/CHERRY_PICK_HEAD"):
                    subprocess.run("git cherry-pick --continue", shell=True, check=True)
                return True
            except subprocess.CalledProcessError:
                print_color("无法继续，请确认冲突已解决并已暂存(git add)", Colors.RED)
        elif choice == 'a':
            subprocess.run("git cherry-pick --abort", shell=True)
            return False

def main():
    print_color("\n=== 选择性同步主项目工具 (Python版) ===", Colors.HEADER)
    
    upstream = "origin"
    private_remote = "myfork"
    
    # 检查Git
    run_cmd("git --version")
    
    # 获取待同步提交
    commits, current_branch = get_commits(upstream, "main")
    
    if not commits:
        print_color("\n✅ 当前分支已是最新，无需同步。", Colors.GREEN)
        print_color("✅ 本地仓库已包含主项目的所有提交。", Colors.GREEN)
        input("按任意键退出...")
        return

    print_color(f"\n⚠️ 发现 {len(commits)} 个未应用的提交 (显示顺序: 新 -> 旧):", Colors.YELLOW)
    print_color("这些提交存在于主项目但尚未应用到当前分支", Colors.YELLOW)
    print("-" * 60)
    
    # 显示提交列表
    for i, c in enumerate(commits):
        index = f"[{i+1}]".ljust(5)
        print(f"{Colors.YELLOW}{index}{Colors.ENDC} {Colors.GREEN}{c['hash']}{Colors.ENDC} - {c['msg']} {Colors.BLUE}({c['date']}){Colors.ENDC}")
    print("-" * 60)

    print("\n功能选项:")
    print("  输入数字 (例如 1) 单个应用")
    print("  输入范围 (例如 1-3) 批量应用")
    print("  输入列表 (例如 1,3,5) 组合应用")
    print("  q 退出")
    
    selection = input("\n请选择要应用的提交: ").strip()
    if selection.lower() == 'q':
        return

    # 解析选择
    selected_indices = []
    try:
        parts = selection.replace('，', ',').split(',') # 支持中文逗号
        for part in parts:
            part = part.strip()
            if not part: continue
            if '-' in part:
                start, end = map(int, part.split('-'))
                # 确保范围正确
                if start > end: start, end = end, start
                selected_indices.extend(range(start-1, end))
            else:
                selected_indices.append(int(part)-1)
    except ValueError:
        print_color("❌ 输入格式错误，请输入数字", Colors.RED)
        return

    # 过滤无效索引并去重
    selected_indices = sorted(list(set([i for i in selected_indices if 0 <= i < len(commits)])))
    
    if not selected_indices:
        print_color("❌ 未选择有效提交", Colors.YELLOW)
        return

    # 关键：按时间正序应用（从旧到新），即列表索引从大到小
    # git log显示的是最新的在前面（索引0），最旧的在后面（索引N）
    # 为了避免依赖问题，应该先应用旧的
    selected_indices.sort(reverse=True)

    print_color(f"\n即将应用 {len(selected_indices)} 个提交...", Colors.CYAN)
    print_color("应用顺序: 从旧到新（避免依赖问题）", Colors.BLUE)
    
    success_list = []
    failed_list = []
    
    for idx in selected_indices:
        c = commits[idx]
        print_color(f"\n正在应用 [{idx+1}]: {c['hash']} - {c['msg']}", Colors.BLUE)
        
        try:
            subprocess.run(f"git cherry-pick {c['hash']}", shell=True, check=True)
            print_color("✅ 成功!", Colors.GREEN)
            success_list.append(c)
        except subprocess.CalledProcessError:
            if resolve_conflict():
                print_color("✅ 成功解决并应用!", Colors.GREEN)
                success_list.append(c)
            else:
                print_color(f"⚠️ 跳过 {c['hash']}", Colors.YELLOW)
                failed_list.append(c)
    
    # 显示详细总结
    print_color("\n" + "="*70, Colors.HEADER)
    print_color("📊 应用结果总结", Colors.HEADER)
    print_color("="*70, Colors.HEADER)
    
    print_color(f"\n✅ 成功应用: {len(success_list)}/{len(selected_indices)} 个提交", Colors.GREEN)
    if success_list:
        for c in success_list:
            print(f"  ✓ {Colors.GREEN}{c['hash']}{Colors.ENDC} - {c['msg']}")
    
    if failed_list:
        print_color(f"\n❌ 失败/跳过: {len(failed_list)}/{len(selected_indices)} 个提交", Colors.RED)
        for c in failed_list:
            print(f"  ✗ {Colors.RED}{c['hash']}{Colors.ENDC} - {c['msg']}")
        print_color("\n💡 提示: 失败的提交可以稍后单独重试", Colors.YELLOW)
    else:
        print_color("\n🎉 所有选择的提交都已成功应用！", Colors.GREEN)
    
    print_color("\n" + "="*70, Colors.HEADER)
    
    # 推送提示
    current = run_cmd("git branch --show-current")
    print_color(f"\n当前分支: {current}", Colors.CYAN)
    push = input(f"是否推送到私人仓库 ({private_remote})? (y/n): ").lower().strip()
    if push == 'y':
        print_color("正在推送...", Colors.CYAN)
        subprocess.run(f"git push {private_remote} {current}", shell=True)
        print_color("✅ 推送完成", Colors.GREEN)
    
    input("\n按任意键退出...")

if __name__ == "__main__":
    main()
