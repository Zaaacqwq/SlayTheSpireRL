from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor, as_completed
import json, os, random, time
from sts2rl.engine import EngineClient, RunConfig

ROOT=os.path.join(os.path.dirname(__file__), '..', 'external', 'sts2-cli')
CMD=['dotnet','run','--no-build','--project','src/Sts2Headless/Sts2Headless.csproj']
CHARS=['Ironclad','Silent','Defect','Necrobinder','Regent']

def run(job):
 c,i=job; seed=f'm1-a0-{c}-{i}'; steps=0; start=time.perf_counter()
 try:
  with EngineClient(CMD,cwd=ROOT,timeout=10,env={'STS2_GAME_DIR':os.environ['STS2_GAME_DIR']}) as e:
   s=e.reset(RunConfig(c,seed)); rng=random.Random(f'{c}:{i}')
   while steps<2000 and s.phase!='game_over': s=e.step(rng.choice(s.candidates)).state; steps+=1
   return {'character':c,'index':i,'seed':seed,'outcome':s.raw.get('victory') if s.phase=='game_over' else None,'steps':steps,'error':None,'seconds':round(time.perf_counter()-start,2)}
 except Exception as x:
  return {'character':c,'index':i,'seed':seed,'outcome':None,'steps':steps,'error':type(x).__name__,'seconds':round(time.perf_counter()-start,2)}

if __name__=='__main__':
 if not os.environ.get('STS2_GAME_DIR'): raise SystemExit('STS2_GAME_DIR required')
 jobs=[(c,i) for c in CHARS for i in range(200)]
 out=[]; start=time.perf_counter()
 with ThreadPoolExecutor(max_workers=16) as pool:
  for f in as_completed([pool.submit(run,j) for j in jobs]): out.append(f.result())
 os.makedirs('rl/runs',exist_ok=True)
 with open('rl/runs/m1_a0_1000.json','w') as f: json.dump(out,f,indent=2)
 for c in CHARS:
  rows=[x for x in out if x['character']==c]; print(c,len(rows),sum(x['outcome'] is True for x in rows),sum(x['error'] is not None for x in rows))
 print('TOTAL',len(out),'SECONDS',round(time.perf_counter()-start,1))
