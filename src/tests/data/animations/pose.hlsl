struct VertexAttributes {
    float3 position;
    float3 normal;
    float4 tangent;
};
struct BlendAttributes {
    float4 weights;
    int4 indicies;
};
struct PoseAttributes {
    float3 S;
    float3 T;
    float4 QR;
    float4 QD;
};
RWStructuredBuffer<VertexAttributes> rw_buffer : register(u5);
StructuredBuffer<VertexAttributes> base : register(t50);
StructuredBuffer<BlendAttributes> blend : register(t51);
StructuredBuffer<PoseAttributes> pose : register(t52);
Texture1D<float4> IniParams : register(t120);
#define TIME IniParams[88].x
#define VG_COUNT IniParams[89].x
int i8toi32(uint src);
uint i32toi8(int src);
[numthreads(64, 1, 1)]
void main(uint3 threadID : SV_DispatchThreadID)
{
    uint i = threadID.x;
    BlendAttributes b = blend[i];
    VertexAttributes v = base[i];
    float4 pos = float4(v.position.x, v.position.y, v.position.z, 1.0f);
    float4 normal = float4(v.normal.x, v.normal.y, v.normal.z, 0.0f);
    int frame = (int)TIME;
    float inter = frac(TIME);
    float inter_prev = 1.0f - inter;
    int vg_count = (int)VG_COUNT;
    int4 idx_prev = frame * vg_count + b.indicies;
    int4 idx_next = (frame + 1) * vg_count + b.indicies;
    PoseAttributes p0_prev = pose[idx_prev.x];
    PoseAttributes p1_prev = pose[idx_prev.y];
    PoseAttributes p2_prev = pose[idx_prev.z];
    PoseAttributes p3_prev = pose[idx_prev.w];
    PoseAttributes p0_next = pose[idx_next.x];
    PoseAttributes p1_next = pose[idx_next.y];
    PoseAttributes p2_next = pose[idx_next.z];
    PoseAttributes p3_next = pose[idx_next.w];
    float4 weights = b.weights;
    float3 scale = (p0_prev.S * weights.x + p1_prev.S * weights.y + p2_prev.S * weights.z + p3_prev.S * weights.w) * inter_prev
                 + (p0_next.S * weights.x + p1_next.S * weights.y + p2_next.S * weights.z + p3_next.S * weights.w) * inter;
    float3 bias = (p0_prev.T * weights.x + p1_prev.T * weights.y + p2_prev.T * weights.z + p3_prev.T * weights.w) * inter_prev
                + (p0_next.T * weights.x + p1_next.T * weights.y + p2_next.T * weights.z + p3_next.T * weights.w) * inter;
    pos.xyz = pos.xyz * scale + bias;
    float4 qr = p0_prev.QR * weights.x * inter_prev;
    float4 qd = p0_prev.QD * weights.x * inter_prev;
    float sign1 = sign(dot(p0_prev.QR, p1_prev.QR));
    float sign2 = sign(dot(p0_prev.QR, p2_prev.QR));
    float sign3 = sign(dot(p0_prev.QR, p3_prev.QR));
    float sign0_next = sign(dot(p0_prev.QR, p0_next.QR));
    float sign1_next = sign(dot(p0_prev.QR, p1_next.QR));
    float sign2_next = sign(dot(p0_prev.QR, p2_next.QR));
    float sign3_next = sign(dot(p0_prev.QR, p3_next.QR));
    qr += p1_prev.QR * weights.y * inter_prev * sign1;
    qd += p1_prev.QD * weights.y * inter_prev * sign1;
    qr += p2_prev.QR * weights.z * inter_prev * sign2;
    qd += p2_prev.QD * weights.z * inter_prev * sign2;
    qr += p3_prev.QR * weights.w * inter_prev * sign3;
    qd += p3_prev.QD * weights.w * inter_prev * sign3;
    qr += p0_next.QR * weights.x * inter * sign0_next;
    qd += p0_next.QD * weights.x * inter * sign0_next;
    qr += p1_next.QR * weights.y * inter * sign1_next;
    qd += p1_next.QD * weights.y * inter * sign1_next;
    qr += p2_next.QR * weights.z * inter * sign2_next;
    qd += p2_next.QD * weights.z * inter * sign2_next;
    qr += p3_next.QR * weights.w * inter * sign3_next;
    qd += p3_next.QD * weights.w * inter * sign3_next;
    float qr_len = length(qr);
    if (qr_len < 1e-6f) qr_len = 1e-6f;
    qr /= qr_len;
    qd /= qr_len;
    float qx = qr.x, qy = qr.y, qz = qr.z, qw = qr.w;
    float qdx = qd.x, qdy = qd.y, qdz = qd.z, qdw = qd.w;
    float m00 = 1.0f - 2.0f*qy*qy - 2.0f*qz*qz;
    float m10 = 2.0f*(qx*qy + qw*qz);
    float m20 = 2.0f*(qx*qz - qw*qy);
    float t0 = 2.0f*(-qdw*qx + qdx*qw - qdy*qz + qdz*qy);
    float m01 = 2.0f*(qx*qy - qw*qz);
    float m11 = 1.0f - 2.0f*qx*qx - 2.0f*qz*qz;
    float m21 = 2.0f*(qy*qz + qw*qx);
    float t1 = 2.0f*(-qdw*qy + qdx*qz + qdy*qw - qdz*qx);
    float m02 = 2.0f*(qx*qz + qw*qy);
    float m12 = 2.0f*(qy*qz - qw*qx);
    float m22 = 1.0f - 2.0f*qx*qx - 2.0f*qy*qy;
    float t2 = 2.0f*(-qdw*qz - qdx*qy + qdy*qx + qdz*qw);
    float4 pos_result;
    pos_result.x = m00*pos.x + m01*pos.y + m02*pos.z + t0*pos.w;
    pos_result.y = m10*pos.x + m11*pos.y + m12*pos.z + t1*pos.w;
    pos_result.z = m20*pos.x + m21*pos.y + m22*pos.z + t2*pos.w;
    pos_result.w = 1.0f;
    float4 normal_result;
    normal_result.x = m00*normal.x + m01*normal.y + m02*normal.z;
    normal_result.y = m10*normal.x + m11*normal.y + m12*normal.z;
    normal_result.z = m20*normal.x + m21*normal.y + m22*normal.z;
    normal_result.w = 0.0f;
    rw_buffer[i].position = float3(pos_result.x, pos_result.y, pos_result.z);
    rw_buffer[i].normal = normalize(float3(normal_result.x, normal_result.y, normal_result.z));
}
int i8toi32(uint src) {
    return (src & 0x80) ? (src | 0xffffff80) : (src & 0x7f);
}
uint i32toi8(int src) {
    src = clamp(src, -128, 127);
    return (uint)src & 0xff;
}
