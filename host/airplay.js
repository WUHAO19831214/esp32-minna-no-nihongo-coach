// Official Music scripting API, JSON arguments only. No subscription URLs or credentials.
function run(argv) {
  const d=JSON.parse(argv[0]), m=Application('Music');
  const outputs=()=>m.airplayDevices().filter(x=>x.available());
  if(d.action==='outputs')return JSON.stringify({ok:true,items:outputs().map(x=>({uid:String(x.id()),name:x.name(),selected:x.selected()}))});
  if(d.action==='seek'){if(m.currentTrack().persistentID()===d.id)m.playerPosition=d.position;return JSON.stringify({ok:true});}
  if(d.action==='stop') {
    let owns=false;try{owns=m.currentTrack().persistentID()===d.id;}catch(_){}
    if(owns){m.stop();}
    return JSON.stringify({ok:true,stopped:owns});
  }
  if(d.action==='state'){let id=null;try{id=m.currentTrack().persistentID();}catch(_){} return JSON.stringify({ok:true,status:m.playerState(),position:m.playerPosition(),id:id});}
  throw Error('未知操作');
}
