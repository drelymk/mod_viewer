[numthreads(64, 1, 1)]
void main(uint3 threadID : SV_DispatchThreadID) {
  float4 pos = float4(v.position.x, -v.position.z, v.position.y, 1.0f);
  float4 normal = float4(v.normal.x, -v.normal.z, v.normal.y, 0.0f);
  rw_buffer[i].position = float3(pos_result.x, pos_result.z, -pos_result.y);
  rw_buffer[i].normal = normalize(float3(normal_result.x, normal_result.z,
                                         -normal_result.y));
}
