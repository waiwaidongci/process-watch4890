import {request} from './client';
import {Cover} from '../types/domain';

const base=(runId:number)=>`/runs/${runId}/cover`;

export const getCover=(runId:number)=>request<Cover>(base(runId));

export const addCoverCause=(runId:number,data:{title:string;cost:number;signals:{sequence:number;kind:string}[]})=>
  request<{id:number;invalidated:boolean}>(`${base(runId)}/causes`,{method:'POST',body:JSON.stringify(data)});

export const removeCoverCause=(runId:number,causeId:number)=>
  request<{invalidated:boolean}>(`${base(runId)}/causes/${causeId}`,{method:'DELETE'});

export const addCoverConflict=(runId:number,data:{cause_a_id:number;cause_b_id:number})=>
  request<{id:number;invalidated:boolean}>(`${base(runId)}/conflicts`,{method:'POST',body:JSON.stringify(data)});

export const removeCoverConflict=(runId:number,conflictId:number)=>
  request<{invalidated:boolean}>(`${base(runId)}/conflicts/${conflictId}`,{method:'DELETE'});

export const runCoverSearch=(runId:number,nodeBudget:number)=>
  request<Cover>(`${base(runId)}/search`,{method:'POST',body:JSON.stringify({node_budget:nodeBudget})});

export const resetCoverSearch=(runId:number)=>
  request<{reset:boolean}>(`${base(runId)}/search`,{method:'DELETE'});
