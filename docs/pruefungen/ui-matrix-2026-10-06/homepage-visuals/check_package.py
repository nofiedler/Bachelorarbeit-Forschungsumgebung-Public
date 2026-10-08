import os,tempfile,shutil,subprocess,zipfile,json
from pathlib import Path
from PIL import Image
root=Path('/work');result={}
for f in (root/'src/research_env/static/logos').glob('*.png'):
 im=Image.open(f);result[f.name]={'mode':im.mode,'size':im.size,'alpha_range':im.getchannel('A').getextrema()}
with tempfile.TemporaryDirectory() as d:
 p=Path(d);shutil.copytree(root/'src',p/'src');shutil.copyfile(root/'pyproject.toml',p/'pyproject.toml')
 build=subprocess.run(['python','-m','pip','wheel','--no-deps','--no-build-isolation',str(p),'-w',str(p/'dist')],stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
 if build.returncode: print(build.stdout.decode());build.check_returncode()
 wheel=next((p/'dist').glob('*.whl'))
 with zipfile.ZipFile(wheel) as z:
  names=set(z.namelist()); expected=[f for f in (root/'src/research_env/static').rglob('*') if f.is_file() and f.parent.name in {'logos','fonts'}]
  expected.append(root/'src/research_env/templates/pipeline_diagram.html')
  for f in expected:
   name=str(f.relative_to(root/'src'));assert name in names,name;assert z.read(name)==f.read_bytes(),name
 result['wheel_files_verified']=len(expected)
print(json.dumps(result,indent=2))
