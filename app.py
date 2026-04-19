import streamlit as st
import google.generativeai as genai
import pandas as pd
import plotly.express as px
import pdfplumber
import requests
import re

# ==========================================
# 1. 보안 및 API 설정
# ==========================================
try:
    API_KEY = st.secrets["GEMINI_API_KEY"]
    GSHEET_SCRIPT_URL = st.secrets["GSHEET_SCRIPT_URL"]
    genai.configure(api_key=API_KEY)
    model = genai.GenerativeModel('gemini-2.5-flash')
except:
    st.error("⚠️ Secrets 설정 정보를 확인해주세요.")

if "analysis_result" not in st.session_state: st.session_state.analysis_result = ""
if "chat_history" not in st.session_state: st.session_state.chat_history = []

# ==========================================
# 2. 인쇄 전용 CSS (화면/인쇄 완벽 분리)
# ==========================================
st.markdown("""
    <style>
    /* 화면에서는 인쇄용 리포트를 숨김 */
    @media screen {
        .print-area { display: none !important; }
    }
    /* 인쇄 시 스트림릿 UI를 싹 지우고 리포트만 노출 */
    @media print {
        header, footer, [data-testid="stSidebar"], .stTabs, .stChatInputContainer, button {
            display: none !important;
        }
        .main .block-container {
            padding: 0 !important;
            max-width: 100% !important;
        }
        .print-area {
            display: block !important;
            width: 100% !important;
            color: black !important;
            background-color: white !important;
            font-size: 11pt !important;
            line-height: 1.7 !important;
        }
        @page { margin: 2cm; }
    }
    </style>
    """, unsafe_allow_html=True)

# ==========================================
# 3. 데이터 가공 함수 (기능 유지)
# ==========================================
def sync_knowledge(new_content=None):
    try:
        if new_content: requests.post(GSHEET_SCRIPT_URL, json={"content": new_content})
        response = requests.get(GSHEET_SCRIPT_URL)
        return response.text if response.status_code == 200 else ""
    except: return ""

def process_performance_data(file):
    xls = pd.ExcelFile(file)
    i_df, m_df = pd.DataFrame(), pd.DataFrame()
    if '학생부현황' in xls.sheet_names:
        df = pd.read_excel(xls, sheet_name='학생부현황')
        res = []
        for g in [1.0, 2.0, 3.0]:
            for s_idx, (u_c, r_c) in enumerate([(3, 5), (10, 12)], 1):
                sub = df[df.iloc[:, 0] == g]
                u_s, w_s = 0, 0
                for _, row in sub.iterrows():
                    try:
                        u, r = float(row.iloc[u_c]), str(row.iloc[r_c]).strip()
                        m = re.search(r'^[1-9]', r)
                        if m: u_s += u; w_s += (u * float(m.group()))
                    except: continue
                if u_s > 0: res.append({"학기": f"{int(g)}-{s_idx}", "등급": round(w_s/u_s, 2)})
        i_df = pd.DataFrame(res)
    if '수능모의고사' in xls.sheet_names:
        df_m = pd.read_excel(xls, sheet_name='수능모의고사')
        m_res = []
        for _, row in df_m.iterrows():
            try:
                txt = str(row.iloc[0])
                g_m, d_m = re.search(r'(\d)학년', txt), re.search(r'\((\d{2})-(\d{2})\)', txt)
                if g_m and d_m:
                    eng = str(row.iloc[10]); eng_v = re.search(r'[1-9]', eng)
                    eng_s = 100 - (int(eng_v.group()) - 1) * 10 if eng_v else 0
                    m_res.append({"key": int(f"{d_m.group(1)}{d_m.group(2)}"), "시험": f"{g_m.group(1)}학년 {d_m.group(2)}월", "국어": float(row.iloc[4]), "수학": float(row.iloc[8]), "영어": eng_s, "탐구": float(row.iloc[13])})
            except: continue
        m_df = pd.DataFrame(m_res).sort_values("key").drop(columns="key") if m_res else pd.DataFrame()
    return i_df, m_df

# ==========================================
# 4. 메인 UI 및 농어촌 옵션
# ==========================================
st.set_page_config(page_title="진학 마스터", layout="wide")
st.title("🎓 고3 대입 전문 컨설팅")

with st.sidebar:
    st.header("📋 학생 데이터 입력")
    target_major = st.text_input("희망 학과", placeholder="예: 간호학과")
    excel_file = st.file_uploader("1. 성적 엑셀", type=["xlsx"])
    pdf_file = st.file_uploader("2. 생기부 PDF", type="pdf")
    is_rural = st.checkbox("🌾 농어촌 전형 대상자 여부", value=False)
    
    st.divider()
    st.header("📚 지식 데이터베이스")
    ref_file = st.file_uploader("자료 업로드", type=["pdf", "xlsx"])
    if st.button("💾 영구 저장"):
        if ref_file:
            with st.spinner("저장 중..."):
                extracted_text = f"\n[자료: {ref_file.name}]\n"
                if ref_file.name.endswith(".pdf"):
                    with pdfplumber.open(ref_file) as p: extracted_text += "".join([pg.extract_text() for pg in p.pages])
                else:
                    xls_ref = pd.ExcelFile(ref_file)
                    for s in xls_ref.sheet_names: extracted_text += f"\n--- 시트: {s} ---\n{pd.read_excel(xls_ref, s).to_string()}\n"
                sync_knowledge(extracted_text); st.success("동기화 완료!")

# ==========================================
# 5. 분석 로직 (프롬프트 강제성 부여)
# ==========================================
if excel_file and pdf_file and target_major:
    if not st.session_state.analysis_result:
        with st.spinner('보수적 정밀 분석 리포트 생성 중...'):
            i_df, m_df = process_performance_data(excel_file)
            with pdfplumber.open(pdf_file) as p: pdf_text = "".join([pg.extract_text() for pg in p.pages])
            k_base = sync_knowledge()
            
            rural_inst = "이 학생은 [농어촌 전형] 대상자이므로, 대입 전략 수립 시 농어촌 전형 지원 분석을 반드시 포함할 것." if is_rural else ""
            
            prompt = f"""
            지방 일반고 전문 컨설턴트로서 {target_major} 지망 학생을 보수적으로 분석함.
            {rural_inst}
            [PART 1: 종합 진단] 성적 분석 및 전형 적합성 기술.
            [PART 2: 대입 전략] 대학 라인 제안 및 추천 도서.
            [PART 3: 심화 탐구] 반드시 "주제:", "종적/횡적 근거:", "탐구 방법:" 이라는 키워드를 사용하여 3가지 주제 작성.
            [PART 4: 면접 대비] 반드시 "질문:", "모범 답안:", "준비 방법:" 이라는 키워드를 사용하여 3개 문항 작성.
            
            리포트의 가장 마지막 줄에는 반드시 아래 형식으로 전형 비중 태그를 달 것 (띄어쓰기 포함 준수):
            @PIE [교과: 50%, 정시: 30%, 종합: 20%] @
            
            데이터: 내신({i_df.to_string()}), 모의고사({m_df.to_string()}), 생기부({pdf_text[:15000]}), 누적지식({k_base[:10000]})
            """
            response = model.generate_content(prompt)
            st.session_state.analysis_result = response.text
            st.session_state.i_df, st.session_state.m_df = i_df, m_df

    # 결과 정규표현식 파싱 (어떤 변형이 와도 잡아냄)
    res = st.session_state.analysis_result
    clean_res = re.sub(r'@.*?@', '', res, flags=re.DOTALL) # 태그 제거된 순수 텍스트

    # 탭 분리 로직 (안전한 정규식 스플릿)
    part1_2 = re.split(r'\[PART 3', clean_res)[0]
    p3_raw = re.split(r'\[PART 3.*?\]', clean_res)[1] if "[PART 3" in clean_res else ""
    p4_raw = ""
    if "[PART 4" in p3_raw:
        split_p34 = re.split(r'\[PART 4.*?\]', p3_raw)
        p3_raw = split_p34[0]
        p4_raw = split_p34[1]

    tab1, tab2, tab3, tab4 = st.tabs(["📝 진단 및 전략", "🚀 심화 탐구 가이드", "💬 실시간 상담", "🖨️ 핵심 요약"])

    # ------------------ Tab 1: 진단 및 전략 ------------------
    with tab1:
        st.subheader("📊 성적 및 전형 분석")
        c1, c2, c3 = st.columns(3)
        if not st.session_state.i_df.empty: c1.plotly_chart(px.line(st.session_state.i_df, x="학기", y="등급", markers=True, range_y=[9, 1], title="내신 추이"), use_container_width=True)
        if not st.session_state.m_df.empty: c2.plotly_chart(px.line(st.session_state.m_df, x="시험", y=["국어", "수학", "영어", "탐구"], markers=True, title="모의고사 추이", range_y=[0, 100]), use_container_width=True)
        
        # [완벽 복구] 파이 차트 강제 추출
        pie_raw = re.search(r'@PIE\s*\[(.*?)\]\s*@', res)
        if pie_raw:
            try:
                p_data = [{"전형": k.strip(), "비중": int(re.sub(r'[^0-9]', '', v))} for k, v in [p.split(':') for p in pie_raw.group(1).split(',')]]
                c3.plotly_chart(px.pie(pd.DataFrame(p_data), values="비중", names="전형", hole=0.4, title="추천 전형"), use_container_width=True)
            except: pass
            
        st.markdown(part1_2.replace("[PART 1:", "### 📝 [PART 1]").replace("[PART 2:", "### 🎯 [PART 2]"))

    # ------------------ Tab 2: 심화 탐구 가이드 ------------------
    with tab2:
        if p3_raw:
            st.markdown("### 🚀 [PART 3] 생기부 기반 심화 탐구 로드맵")
            # 띄어쓰기가 틀려도 무조건 이모지로 교체하는 정규식 적용
            f_p3 = re.sub(r'(?i)주제\s*:', '#### 📍 주제:', p3_raw)
            f_p3 = re.sub(r'(?i)종적/횡적\s*근거\s*:', '🔍 **종적/횡적 근거:**', f_p3)
            f_p3 = re.sub(r'(?i)탐구\s*방법\s*:', '🛠️ **탐구 방법:**', f_p3)
            st.markdown(f_p3)
            
        if p4_raw:
            st.markdown("---")
            st.markdown("### 🎤 [PART 4] 면접 예상 질문 가이드")
            f_p4 = re.sub(r'(?i)질문\s*:', '#### ❓ 질문:', p4_raw)
            f_p4 = re.sub(r'(?i)모범\s*답안\s*:', '✅ **모범 답안:**', f_p4)
            f_p4 = re.sub(r'(?i)준비\s*방법\s*:', '🛠️ **준비 방법:**', f_p4)
            st.markdown(f_p4)

    # ------------------ Tab 3: 실시간 상담 ------------------
    with tab3:
        for msg in st.session_state.chat_history:
            with st.chat_message(msg["role"]): st.markdown(msg["content"])
        if p_chat := st.chat_input("추가 상담 질문..."):
            st.session_state.chat_history.append({"role": "user", "content": p_chat})
            with st.chat_message("user"): st.markdown(p_chat)
            with st.chat_message("assistant"):
                ans = model.generate_content(f"배경: {res}\n질문: {p_chat}")
                st.markdown(ans.text); st.session_state.chat_history.append({"role": "assistant", "content": ans.text})

    # ------------------ Tab 4: 핵심 요약 및 인쇄 ------------------
    with tab4:
        st.markdown("### 🖨️ 인쇄용 핵심 요약 리포트")
        # [완벽 복구] 스트림릿 버튼 제약을 우회하는 순수 HTML a 태그 방식 적용
        st.markdown("""
            <a href="javascript:window.print();" style="display:block; background-color:#ff4b4b; color:white; text-align:center; padding:12px; border-radius:8px; text-decoration:none; font-weight:bold; margin-bottom:20px;">
                📄 PDF 인쇄창 열기 (클릭)
            </a>
            """, unsafe_allow_html=True)
        st.info("💡 위 버튼을 누르면 인쇄 미리보기 창이 뜹니다. (대상: PDF로 저장)")

    # ------------------ 인쇄 전용 영역 (화면에선 숨김) ------------------
    st.markdown(f"""
    <div class="print-area">
        <h1 style="text-align: center;">대입 컨설팅 결과 리포트 {"(농어촌 대상)" if is_rural else ""}</h1>
        <p style="text-align: right;">지원학과: {target_major}</p>
        <hr>
        <div style="white-space: pre-wrap; font-family: sans-serif; line-height: 1.8;">{clean_res}</div>
    </div>
    """, unsafe_allow_html=True)
