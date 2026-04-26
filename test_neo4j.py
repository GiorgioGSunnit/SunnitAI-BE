from dotenv import load_dotenv
load_dotenv('.env.local')
import os
from neo4j import GraphDatabase

uri = os.getenv('NEO4J_URI')
user = os.getenv('NEO4J_USER', 'neo4j')
pwd = os.getenv('NEO4J_PASSWORD')

print(f'URI : {uri}')
print(f'User: {user}')
print(f'Pass length: {len(pwd) if pwd else 0}')
print(f'Pass bytes : {pwd.encode() if pwd else b"MISSING"}')

driver = GraphDatabase.driver(uri, auth=(user, pwd))
with driver.session() as s:
    r = s.run('RETURN 1 AS n')
    print('Connected! Result:', r.single()['n'])
driver.close()
