/**
 * ViralCut FFmpeg Worker V2.2
 * This is a small, independent worker implementing the ffmpeg.wasm 0.12
 * message contract. It avoids webpack's dynamic-import rewrite that causes
 * "Cannot find module 'blob:...'" in CDN-hosted UMD worker bundles.
 * ffmpeg.wasm is MIT licensed: https://github.com/ffmpegwasm/ffmpeg.wasm
 */
'use strict';
let ffmpegCore=null;
const CORE_DEFAULT='https://cdn.jsdelivr.net/npm/@ffmpeg/core@0.12.10/dist/esm/ffmpeg-core.js';
const reply=(id,type,data,transfer=[])=>self.postMessage({id,type,data},transfer);

async function loadCore({coreURL,wasmURL,workerURL}={}) {
  const first=!ffmpegCore;
  if(ffmpegCore)return false;
  const scriptURL=coreURL||CORE_DEFAULT;
  let factory;
  try {
    // Native dynamic import: no webpack module resolver involved.
    const module=await import(/* @vite-ignore */ scriptURL);
    factory=module.default;
  } catch(error) {
    throw new Error('Gagal import FFmpeg Core (ESM): '+String(error?.message||error));
  }
  if(typeof factory!=='function')throw new Error('FFmpeg Core tidak mengirim fungsi inisialisasi.');
  const resolvedWasmURL=wasmURL||scriptURL.replace(/\.js$/,'.wasm');
  const resolvedWorkerURL=workerURL||scriptURL.replace(/\.js$/,'.worker.js');
  ffmpegCore=await factory({
    mainScriptUrlOrBlob:`${scriptURL}#${btoa(JSON.stringify({wasmURL:resolvedWasmURL,workerURL:resolvedWorkerURL}))}`
  });
  ffmpegCore.setLogger(data=>self.postMessage({type:'LOG',data}));
  ffmpegCore.setProgress(data=>self.postMessage({type:'PROGRESS',data}));
  return first;
}

self.onmessage=async event=>{
  const {id,type,data:d={}}=event.data||{};
  try {
    if(type!=='LOAD'&&!ffmpegCore)throw Error('FFmpeg Core belum dimuat.');
    let result;
    switch(type) {
      case 'LOAD': result=await loadCore(d);break;
      case 'EXEC':
        ffmpegCore.setTimeout(d.timeout??-1);
        ffmpegCore.exec(...d.args);
        result=ffmpegCore.ret;
        ffmpegCore.reset();
        break;
      case 'FFPROBE':
        ffmpegCore.setTimeout(d.timeout??-1);
        ffmpegCore.ffprobe(...d.args);
        result=ffmpegCore.ret;
        ffmpegCore.reset();
        break;
      case 'WRITE_FILE':ffmpegCore.FS.writeFile(d.path,d.data);result=true;break;
      case 'READ_FILE':result=ffmpegCore.FS.readFile(d.path,{encoding:d.encoding||'binary'});break;
      case 'DELETE_FILE':ffmpegCore.FS.unlink(d.path);result=true;break;
      case 'RENAME':ffmpegCore.FS.rename(d.oldPath,d.newPath);result=true;break;
      case 'CREATE_DIR':ffmpegCore.FS.mkdir(d.path);result=true;break;
      case 'DELETE_DIR':ffmpegCore.FS.rmdir(d.path);result=true;break;
      case 'LIST_DIR':
        result=ffmpegCore.FS.readdir(d.path).map(name=>({
          name,isDir:ffmpegCore.FS.isDir(ffmpegCore.FS.stat(`${d.path}/${name}`).mode)
        }));
        break;
      case 'MOUNT': {
        const fs=ffmpegCore.FS.filesystems[d.fsType];
        result=Boolean(fs);
        if(fs)ffmpegCore.FS.mount(fs,d.options,d.mountPoint);
        break;
      }
      case 'UNMOUNT':ffmpegCore.FS.unmount(d.mountPoint);result=true;break;
      default: throw new Error('Tipe perintah tidak dikenal: '+type);
    }
    reply(id,type,result,result instanceof Uint8Array?[result.buffer]:[]);
  }catch(error){
    console.error('FFmpeg worker',type,error);
    reply(id,'ERROR',String(error?.message||error));
  }
};
