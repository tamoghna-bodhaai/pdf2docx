import { execFileSync } from "node:child_process";

export const images: string[] = JSON.parse(execFileSync("python3", ["-c", `
from PIL import Image, ImageDraw
import io, base64, json
result=[]
for w,h,rotation in [(240,1600,1),(1000,400,1),(500,500,1),(900,400,6)]:
 im=Image.new('RGB',(w,h),'#f3d8af')
 draw=ImageDraw.Draw(im)
 draw.rectangle((8,8,w-9,h-9),outline='#8c4020',width=8)
 draw.line((0,0,w,h),fill='#456f69',width=18)
 exif=Image.Exif(); exif[274]=rotation
 out=io.BytesIO(); im.save(out,format='JPEG',exif=exif)
 result.append(base64.b64encode(out.getvalue()).decode())
print(json.dumps(result))
`], { encoding: "utf8" }));
