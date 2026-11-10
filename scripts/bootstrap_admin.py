"""One-time initial administrator creation; password is read from the terminal."""
from pathlib import Path
import argparse,getpass,sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.security.store import initialize_control,bootstrap_superadmin

if __name__ == '__main__':
    parser=argparse.ArgumentParser(description='初始化首位集团超级管理员，不输出或保存明文密码。')
    parser.add_argument('--username',required=True)
    args=parser.parse_args()
    password=getpass.getpass('初始密码（至少12位，含英文字母和数字）：')
    if password != getpass.getpass('再次输入密码：'):
        raise SystemExit('两次密码不一致。')
    initialize_control()
    ident=bootstrap_superadmin(args.username,password)
    print('初始化完成。已存在超管时不会覆盖账户；首次登录须修改密码。账户标识：'+str(ident))
