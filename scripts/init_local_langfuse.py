"""Create local credentials once; never print secrets or modify the NH DB .env."""

import json
import secrets
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
folder=ROOT/'infra/langfuse'
destination=folder/'.env.local'
config=ROOT/'data/langgraph-rag/langfuse-config.private.json'
if destination.exists() or config.exists():
    raise SystemExit('Credentials already exist; reuse them, do not rotate implicitly.')
values={key:secrets.token_hex(24) for key in (
    'LF_DB_PASSWORD','LF_CLICKHOUSE_PASSWORD','LF_REDIS_PASSWORD','LF_MINIO_PASSWORD',
    'LF_SALT','LF_NEXTAUTH_SECRET','LF_LOGIN_PASSWORD')}
values['LF_ENCRYPTION_KEY']=secrets.token_hex(32)
values['LF_PUBLIC_KEY']='pk-lf-'+secrets.token_hex(16)
values['LF_SECRET_KEY']='sk-lf-'+secrets.token_hex(24)
destination.write_text(''.join(f'{k}={v}\n' for k,v in values.items()),encoding='utf8')
config.parent.mkdir(parents=True,exist_ok=True)
config.write_text(json.dumps(dict(base_url='http://localhost:3008',public_key=values['LF_PUBLIC_KEY'],
                                  secret_key=values['LF_SECRET_KEY'],project_id='nh-table-rag'),indent=2),encoding='utf8')
(folder/'access.local.txt').write_text(
    'Dashboard: http://localhost:3008\nEmail: demo@nh.local\nPassword: '+values['LF_LOGIN_PASSWORD']+
    '\nClient configuration: '+str(config)+'\nLocal demonstration only. Do not share this file.\n',encoding='utf8')
print(json.dumps(dict(base_url='http://localhost:3008',login_email='demo@nh.local',
                      credentials_file=str(folder/'access.local.txt'),config_file=str(config))))
