import streamlit as st
import google.generativeai as genai
import pandas as pd
import plotly.express as px
import pdfplumber
import requests
import re
import io

# ==========================================
# 1. 보안 및 API 설정
# ==========================================
try:
    # Streamlit Secrets에서 정보를 가져옵니다.
    API_KEY = st.secrets["GEMINI_API_KEY"]
    GSHEET_SCRIPT_URL = st.secrets["GSHEET_SCRIPT_URL"]
    
    genai.configure(api_key=API_KEY)
    # Gemini 3 Flash 모델 사용 (유료 티어 성능)
    model = genai.GenerativeModel('gemini-2.5-flash')
except:
    st.error("⚠️ Secrets 설정(GEMINI_API_KEY, GSHEET_SCRIPT_URL)이 누락되었습니다.")

# 세션 상태 초기화
if "analysis_result" not in st.session_state: st.session_state.analysis_result = ""
if "chat_history" not in st.session_state: st.session_state.chat_history = []

# ==========================================
# 2. 구글 시트 데이터 동기화 함수
# ==========================================
def sync_knowledge(new_content=None):
    """구글 시트에 데이터를 저장(POST)하거나 전체를 불러옵니다(GET)."""
    try:
        if new_content:
            # 새로운 지식 영구 저장 (POST)
            requests.post(GSHEET_SCRIPT_URL, json={"content": new_content})
        
        # 전체 누적 지식 불러오기 (GET)
        response = requests.get(GSHEET_SCRIPT_URL)
        return response.text if response.status_code == 200 else ""
    except:
        return ""

# ==========================================
# 3. 데이터 가공 함수 (성적 엑셀)
# ==========================================
def process_performance_data(file):
    xls = pd.ExcelFile(file)
    i_df, m_df = pd.DataFrame(), pd.DataFrame()
    
    # 내신 데이터 (학생부현황 시트)
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

    # 모의고사 데이터 (수능모의고사 시트)
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
                    m_res.append({
                        "key": int(f"{d_m.group(1)}{d_m.group(2)}"),
                        "시험": f"{g_m.group(1)}학년 {d_m.group(2)}월",
                        "국어": float(row.iloc[4]), "수학": float(row.iloc[8]), "영어": eng_s, "탐구": float(row.iloc[13])
                    })
            except: continue
        m_df = pd.DataFrame(m_res).sort_values("key").drop(columns="key") if m_res else pd.DataFrame()
    return i_df, m_df

# ==========================================
# 4. 메인 UI (V31 레이아웃 복구)
# ==========================================
st.set_page_config(page_title="고3 진학 마스터 V38", layout="wide")
st.title("🎓 고3 대입 전문 컨설팅 솔루션 (V38 - 영구 보관형)")

with st.sidebar:
    st.header("📋 학생 데이터 입력")
    target_major = st.text_input("희망 학과", placeholder="예: 경영학과")
    excel_file = st.file_uploader("1. 성적 엑셀 (필수)", type=["xlsx"])
    pdf_file = st.file_uploader("2. 생기부 PDF (필수)", type="pdf")
    
    st.divider()
    st.header("📚 지식 데이터베이스 구축")
    ref_file = st.file_uploader("학습용 PDF/엑셀 업로드", type=["pdf", "xlsx"])
    
    if st.button("💾 이 파일을 영구 저장하기"):
        if ref_file:
            with st.spinner("지식 추출 및 시트 저장 중..."):
                extracted_text = ""
                if ref_file.name.endswith(".pdf"):
                    with pdfplumber.open(ref_file) as p: extracted_text = "".join([pg.extract_text() for pg in p.pages])
                else:
                    df_ref = pd.read_excel(ref_file); extracted_text = df_ref.to_string()
                
                # 구글 시트에 텍스트 영구 박제
                sync_knowledge(f"\n[자료: {ref_file.name}]\n{extracted_text}")
                st.success("데이터가 구글 시트에 영구 보관되었습니다.")
        else:
            st.warning("저장할 파일을 먼저 선택하세요.")

# ==========================================
# 5. 분석 및 결과 출력
# ==========================================
if excel_file and pdf_file and target_major:
    if not st.session_state.analysis_result:
        with st.spinner('구글 시트 누적 지식 기반 심층 분석 중...'):
            i_df, m_df = process_performance_data(excel_file)
            with pdfplumber.open(pdf_file) as p: pdf_text = "".join([pg.extract_text() for pg in p.pages])
            
            # 구글 시트에서 전체 누적 지식 가져오기
            accumulated_knowledge = sync_knowledge()

            prompt = f"""
            베테랑 입시 교사로서 {target_major} 지망 학생을 분석함. 
            모든 답변은 개괄식 음슴체로 작성하고 전문성을 유지함.
            지방 일반고의 현실을 적극 반영하여 합리적이고 보수적인 대학 라인을 제안함.

            [입력 데이터]
            - 누적 지식 베이스: {accumulated_knowledge[:7000]}
            - 학생 성적: 내신({i_df.to_string()}), 모의고사({m_df.to_string()})
            
            [출력 구조]
            [PART 1: 종합 진단] (내신 추이 및 수능 최저 충족 가시성 정밀 분석)
            [PART 2: 대입 전략] (안정/적정/상향 대학 라인 및 추천 도서 3권 요약)
            [PART 3: 심화 탐구] (가시성 좋은 Step 1, 2, 3 단계별 가이드)
            [PART 4: 면접 대비] (질문-모범답안-준비방법)

            [태그] @PIE [교과:%, 정시:%, 종합:%] @ @PERCENT [00] @
            생기부 내용: {pdf_text[:15000]}
            """
            response = model.generate_content(prompt)
            st.session_state.analysis_result = response.text
            st.session_state.i_df, st.session_state.m_df = i_df, m_df

    # 탭 구성 및 결과 출력
    tab1, tab2, tab3, tab4 = st.tabs(["📝 진단 및 전략", "🎯 탐구 및 면접", "💬 실시간 상담", "🖨️ 핵심 요약"])
    res = st.session_state.analysis_result
    clean_res = re.sub(r'@.*?@', '', res, flags=re.DOTALL)

    with tab1:
        st.subheader("📊 통합 성적 분석")
        c1, c2, c3 = st.columns(3)
        if not st.session_state.i_df.empty: c1.plotly_chart(px.line(st.session_state.i_df, x="학기", y="등급", markers=True, range_y=[9, 1], title="내신 등급 추이"), use_container_width=True)
        if not st.session_state.m_df.empty: c2.plotly_chart(px.line(st.session_state.m_df, x="시험", y=["국어", "수학", "영어", "탐구"], markers=True, title="모의고사 추이", range_y=[0, 100]), use_container_width=True)
        
        pie_raw = re.search(r'@PIE \[(.*?)\] @', res)
        if pie_raw:
            p_data = [{"전형": k.strip(), "비중": int(re.sub(r'[^0-9]', '', v))} for k, v in [it.split(':') for it in pie_raw.group(1).split(',')]]
            c3.plotly_chart(px.pie(pd.DataFrame(p_data), values="비중", names="전형", hole=0.4, title="전형 적합도"), use_container_width=True)
        
        st.markdown(clean_res.split("[PART 3:")[0].replace("[PART 1:", "### 📝 [PART 1]").replace("[PART 2:", "### 🎯 [PART 2]"))

    with tab2:
        if "[PART 3:" in clean_res:
            st.markdown("### 🚀 [PART 3] 핵심 전략 (탐구 로드맵)")
            p3_4 = clean_res.split("[PART 3:")[1].replace("[PART 4:", "### 🎤 [PART 4]")
            st.markdown(p3_4.replace("질문:", "#### ❓ 질문:").replace("모범답안:", "✅ **모범답안:**").replace("준비방법:", "🛠️ **준비방법:**"))

    with tab3:
        for msg in st.session_state.chat_history:
            with st.chat_message(msg["role"]): st.markdown(msg["content"])
        if p_chat := st.chat_input("추가 질문..."):
            st.session_state.chat_history.append({"role": "user", "content": p_chat})
            with st.chat_message("user"): st.markdown(p_chat)
            with st.chat_message("assistant"):
                ans = model.generate_content(f"배경: {res}\n지식: {sync_knowledge()[:3000]}\n질문: {p_chat}")
                st.markdown(ans.text)
                st.session_state.chat_history.append({"role": "assistant", "content": ans.text})

    with tab4:
        st.markdown("# 📋 핵심 요약 리포트 (인쇄용)"); st.info(clean_res)
