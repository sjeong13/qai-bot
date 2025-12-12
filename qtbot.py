
# =====================================================================================
"""
2025-12-11
큐티봇 v1.0

1. 테케봇 v2.1 그대로 가져옴
- 하이브리드 검색: 벡터 검색 + LLM 재랭킹
- Supabase 테이블: test_cases_v21, spec_docs_v21

2. 리스크 사전 검토

3. 의도된 동작인지 검토 (to. CX)

4. 키워드 검색
- 새 탭 페이지에서도

"""
# =====================================================================================

import streamlit as st
import json
from datetime import datetime
import google.generativeai as genai
import os
import pandas as pd
from io import BytesIO, StringIO
from supabase_helpers import (
    get_supabase_client,
    save_test_case_to_supabase,
    save_spec_doc_to_supabase,
    hybrid_search_test_cases,      # ⭐ 하이브리드 검색
    hybrid_search_spec_docs,       # ⭐ 하이브리드 검색
    TABLE_NAME,                     # test_cases_v21
    SPEC_TABLE_NAME,                # spec_docs_v21
    GOOGLE_API_KEY,
    INITIAL_SEARCH_COUNT,
    FINAL_SEARCH_COUNT,
    RERANK_METHOD
)

# Excel 지원 확인
try:
    import openpyxl
    from openpyxl.styles import PatternFill, Font, Alignment, Border, Side
    EXCEL_AVAILABLE = True
except ImportError:
    EXCEL_AVAILABLE = False

# 세션 스테이트 초기화
if 'test_cases' not in st.session_state:
    st.session_state.test_cases = []  # 빈 리스트로 시작

if 'spec_docs' not in st.session_state:
    st.session_state.spec_docs = []  # 빈 리스트로 시작

if 'search_history' not in st.session_state:
    st.session_state.search_history = []

# 카운트 초기화 시 DB에서 실제 값 가져오기
if 'tc_count' not in st.session_state or st.session_state.get('force_reload_tc_count', False):
    supabase = get_supabase_client()
    if supabase:
        try:
            # count() 사용 - 모든 레코드 수를 정확히 반환
            result = supabase.table(TABLE_NAME).select('id', count='exact').execute()
            st.session_state.tc_count = result.count  # count 속성 사용

            # 플래그 초기화
            if 'force_reload_tc_count' in st.session_state:
                del st.session_state.force_reload_tc_count
        except:
            st.session_state.tc_count = 0
    else:
        st.session_state.tc_count = 0

if 'doc_count' not in st.session_state or st.session_state.get('force_reload_doc_count', False):
    supabase = get_supabase_client()  # 다시 가져오기
    if supabase:
        try:
            # count() 사용
            result = supabase.table(SPEC_TABLE_NAME).select('id', count='exact').execute()
            st.session_state.doc_count = result.count  # count 속성 사용

            # 플래그 초기화
            if 'force_reload_doc_count' in st.session_state:
                del st.session_state.force_reload_doc_count
                
        except:
            st.session_state.doc_count = 0
    else:
        st.session_state.doc_count = 0

# 편집 모드 세션 스테이트
if 'editing_test_case_id' not in st.session_state:
    st.session_state.editing_test_case_id = None

if 'editing_spec_doc_id' not in st.session_state:
    st.session_state.editing_spec_doc_id = None

# 페이지 설정
st.set_page_config(
    page_title="큐티봇",
    page_icon="🧑‍🏫",
    layout="wide"
)

# URL 파라미터 확인
query_params = st.query_params

# Streamlit 1.30+ 버전 호환
page = query_params.get("page", "main")
if isinstance(page, list):
    page = page[0]

if 'authenticated' not in st.session_state:
    st.session_state.authenticated = False

if not st.session_state.authenticated:
    st.title("🔒 로그인")
    st.markdown("---")
    
    col1, col2, col3 = st.columns([1, 2, 1])
    
    with col2:
        st.info("💡 비밀번호를 입력하세요.")

        # 비밀번호 입력 후 Enter 키 동작
        with st.form(key="login_form"):
            password = st.text_input(
                "비밀번호",
                type="password",
                placeholder="비밀번호를 입력하세요"
            )
        
            col_a, col_b, col_c = st.columns([1, 1, 1])
            with col_b:
                submit_button = st.form_submit_button("🔓 로그인", type="primary", use_container_width=True)

            if submit_button:
                correct_password = os.environ.get("APP_PASSWORD", "qabot2025")
                
                if password == correct_password:
                    st.session_state.authenticated = True
                    st.success("✅ 로그인 성공!")
                    st.rerun()
                else:
                    st.error("❌ 잘못된 비밀번호입니다.")    
    st.stop()

st.title("🧑‍🏫 큐티봇 (Qa Test Bot)")
st.caption("v2.1 - 하이브리드 검색 버전 🚀")
st.markdown("---")

# ============================================
# 페이지 라우팅
# ============================================

# 테스트 케이스 전체보기 페이지
if page == "test_cases":
    st.header("📝 테스트 케이스 (새 탭)")
    
    # 홈으로 돌아가기 링크
    st.markdown(f'<a href="/" target="_self">🏠 홈으로 돌아가기</a>', unsafe_allow_html=True)
    st.markdown("---")

    # Supabase에서 직접 로드
    supabase = get_supabase_client()
    if supabase:
        try:
            # 1. 전체 개수 조회
            count_result = supabase.table(TABLE_NAME).select('id', count='exact').execute()
            total_count = count_result.count

            st.metric("전체 케이스 수", f"{total_count}개")

            # 2. 충분한 데이터 가져오기 (최근 1000개 - 그룹 5개는 충분히 포함)
            result = supabase.table(TABLE_NAME)\
                .select('*')\
                .order('id', desc=True)\
                .limit(1000)\
                .execute()

            if result.data:
                # 3. group_id별로 그룹핑 (최신순 유지)
                grouped_cases = {}
                ungrouped_cases = []

                for row in result.data:
                    tc_data = row.get('data', {})
                    group_id = tc_data.get('group_id')

                    if group_id:
                        # 그룹이 있는 케이스
                        if group_id not in grouped_cases:
                            grouped_cases[group_id] = {
                                'rows': [],
                                'category': row.get('category', '미분류'),
                                'input_type': tc_data.get('input_type', 'unknown'),
                                'first_id': row['id'],  # 그룹의 첫 번째 ID (최신)
                                'max_id': row['id']  # 정렬용 (그룹 내 최신 ID)
                            }
                        grouped_cases[group_id]['rows'].append(row)
                    else:
                        # 그룹이 없는 케이스
                        ungrouped_cases.append(row)

                # 4. 그룹을 max_id 기준 내림차순 정렬 (최신 그룹 먼저)
                sorted_groups = sorted(
                    grouped_cases.items(),
                    key=lambda x: x[1]['max_id'],
                    reverse=True
                )

                # 5. 최근 2개 그룹만 선택
                recent_2_groups = sorted_groups[:2]

                # 6. 개별 케이스도 최근 2개만
                recent_2_ungrouped = ungrouped_cases[:2]
                                
                st.markdown("### 📌 최근 등록한 테스트 케이스 (2개)")
                st.markdown("---")

                # 7. 최근 2개 그룹 표시
                if recent_2_groups:
                    for idx, (group_id, group_info) in enumerate(recent_2_groups):
                        rows = group_info['rows']
                        category = group_info['category']
                        input_type = group_info['input_type']
                        first_id = group_info['first_id']

                        # 그룹 내에서 id 기준 오름차순 정렬
                        rows = sorted(rows, key=lambda x: x['id'])
                    
                        # 그룹 제목
                        group_title = f"[{category}] 📊 표 그룹 ({len(rows)}개)"

                        # 고유 키 생성
                        unique_key = f"group_{first_id}_{idx}"

                        with st.expander(group_title, expanded=False):
                            # 수정 모드 체크
                            is_editing = st.session_state.editing_test_case_id == unique_key

                            if is_editing:
                                # 📝 수정 모드
                                st.info("💡 표를 수정하세요. 행을 추가하려면 아래 버튼을 사용하세요.")

                                # 수정용 세션 스테이트 관리
                                edit_session_key = f"edit_df_{unique_key}"

                                # 초기 로드 시에만 데이터 설정
                                if edit_session_key not in st.session_state:
                                    df_data = []
                                    for row in rows:
                                        tc_data = row.get('data', {})
                                        df_data.append({
                                            'NO': tc_data.get('no', ''),
                                            'CATEGORY': tc_data.get('category', ''),
                                            'DEPTH 1': tc_data.get('depth1', ''),
                                            'DEPTH 2': tc_data.get('depth2', ''),
                                            'DEPTH 3': tc_data.get('depth3', ''),
                                            'PRE-CONDITION': tc_data.get('pre_condition', ''),
                                            'STEP': tc_data.get('step', ''),
                                            'EXPECT RESULT': tc_data.get('expect_result', '')
                                        })
                                    st.session_state[edit_session_key] = pd.DataFrame(df_data)

                                # 행 추가 버튼
                                col_add, col_del = st.columns([1, 1])
                                with col_add:
                                    if st.button("➕ 행 추가", key=f"add_row_{unique_key}"):
                                        new_row = pd.DataFrame({
                                            'NO': [''],
                                            'CATEGORY': [''],
                                            'DEPTH 1': [''],
                                            'DEPTH 2': [''],
                                            'DEPTH 3': [''],
                                            'PRE-CONDITION': [''],
                                            'STEP': [''],
                                            'EXPECT RESULT': ['']
                                        })
                                        st.session_state[edit_session_key] = pd.concat(
                                            [st.session_state[edit_session_key], new_row],
                                            ignore_index=True
                                        )
                                        st.rerun()

                                with col_del:
                                    if st.button("🗑️ 마지막 행 삭제", key=f"del_row_{unique_key}"):
                                        if len(st.session_state[edit_session_key]) > 1:
                                            st.session_state[edit_session_key] = st.session_state[edit_session_key].iloc[:-1]
                                            st.rerun()

                                # 데이터 에디터
                                edited_df = st.data_editor(
                                    st.session_state[edit_session_key],
                                    use_container_width=True,
                                    hide_index=True,
                                    key=f"editor_{unique_key}"
                                )

                                # 변경사항 즉시 반영
                                st.session_state[edit_session_key] = edited_df
                    
                                col1, col2 = st.columns(2)
                                with col1:
                                    if st.button("💾 저장", key=f"save_{unique_key}", use_container_width=True):
                                        try:
                                            # 기존 그룹 전체 삭제
                                            for row in rows:
                                                supabase.table(TABLE_NAME).delete().eq('id', row['id']).execute()

                                            # 새로운 데이터로 다시 저장
                                            new_table_data = []
                                            for _, row in edited_df.iterrows():
                                                # 빈 행 필터링 개선
                                                if (pd.isna(row['CATEGORY']) or str(row['CATEGORY']).strip() == '') and \
                                                   (pd.isna(row['DEPTH 1']) or str(row['DEPTH 1']).strip() == ''):
                                                    continue
                                            
                                                new_table_data.append({
                                                    'NO': str(row['NO']),
                                                    'CATEGORY': str(row['CATEGORY']),
                                                    'DEPTH 1': str(row['DEPTH 1']),
                                                    'DEPTH 2': str(row['DEPTH 2']),
                                                    'DEPTH 3': str(row['DEPTH 3']),
                                                    'PRE-CONDITION': str(row['PRE-CONDITION']),
                                                    'STEP': str(row['STEP']),
                                                    'EXPECT RESULT': str(row['EXPECT RESULT'])
                                                })

                                            if new_table_data:
                                                group_test = {
                                                    "group_id": group_id,
                                                    "input_type": input_type,
                                                    # "category": category,
                                                    "category": "입력 그룹",
                                                    "name": f"({len(new_table_data)}개)",
                                                    "table_data": new_table_data
                                                }

                                                saved_count = save_test_case_to_supabase(group_test)

                                                if saved_count > 0:
                                                    st.session_state.editing_test_case_id = None
                                                    # 세션 스테이트 정리
                                                    if edit_session_key in st.session_state:
                                                        del st.session_state[edit_session_key]
                                                    st.success("✅ 수정되었습니다!")
                                                    st.rerun()
                                                else:
                                                    st.error("❌ 저장 실패!")
                                            else:
                                                st.warning("⚠️ 저장할 데이터가 없습니다. CATEGORY 또는 DEPTH 1을 입력하세요.")
                                        except Exception as e:
                                            st.error(f"❌ 수정 실패: {str(e)}")
                                        
                                with col2:
                                    if st.button("❌ 취소", key=f"cancel_{unique_key}", use_container_width=True):
                                        st.session_state.editing_test_case_id = None
                                        # 세션 스테이트 정리
                                        if edit_session_key in st.session_state:
                                            del st.session_state[edit_session_key]
                                        st.rerun()

                            else:
                                # 📖 보기 모드
                                st.write(f"**카테고리:** {category}")
                                st.write(f"**타입:** {input_type}")
                                st.write(f"**개수:** {len(rows)}개")

                                # 표로 보여주기
                                df_data = []
                                for row in rows:
                                    tc_data = row.get('data', {})
                                    df_data.append({
                                        'NO': tc_data.get('no', ''),
                                        'CATEGORY': tc_data.get('category', ''),
                                        'DEPTH 1': tc_data.get('depth1', ''),
                                        'DEPTH 2': tc_data.get('depth2', ''),
                                        'DEPTH 3': tc_data.get('depth3', ''),
                                        'PRE-CONDITION': tc_data.get('pre_condition', ''),
                                        'STEP': tc_data.get('step', ''),
                                        'EXPECT RESULT': tc_data.get('expect_result', '')
                                    })

                                if df_data:
                                    df = pd.DataFrame(df_data)
                                    st.dataframe(df, use_container_width=True, hide_index=True)
                                else:
                                    st.warning("⚠️ 표시할 데이터가 없습니다.")

                                col1, col2 = st.columns(2)
                            
                                # 수정 버튼
                                with col1:
                                    if st.button("✏️ 수정", key=f"edit_{unique_key}", use_container_width=True):
                                        st.session_state.editing_test_case_id = unique_key
                                        st.rerun()
                            
                                # 삭제 버튼
                                with col2:
                                    if st.button("🗑️ 삭제", key=f"delete_{unique_key}", use_container_width=True):
                                        try:
                                            # 1. 그룹 내 모든 케이스 삭제
                                            for row in rows:
                                                supabase.table(TABLE_NAME).delete().eq('id', row['id']).execute()

                                            # 2. 캐시 클리어
                                            st.cache_data.clear()

                                            # 3. 카운트 업데이트
                                            result = supabase.table(TABLE_NAME).select('id', count='exact').execute()
                                            st.session_state.tc_count = result.count  # count 사용
                                        
                                            st.success("✅ 삭제되었습니다!")
                                            st.rerun()
                                        except Exception as e:
                                            st.error(f"❌ 삭제 실패: {str(e)}")
                # 8. 개별 케이스. 그룹 없는 케이스 (줄글 형식 등) (최근 2개)
                if recent_2_ungrouped:
                    st.markdown("### 📝 최근 개별 케이스 (2개)")
                    
                    for row in recent_2_ungrouped:
                        tc_data = row.get('data', {})
                        
                        with st.expander(f"[{row.get('category', '미분류')}] {row.get('name', '제목 없음')}", expanded=False):
                            # 수정 모드 체크
                            is_editing = st.session_state.editing_test_case_id == row['id']
                            
                            if is_editing:
                                # 📝 수정 모드
                                edited_category = st.text_input("카테고리", value=row.get('category', ''), key=f"edit_tc_cat_{row['id']}")
                                edited_name = st.text_input("이름", value=row.get('name', ''), key=f"edit_tc_name_{row['id']}")
                                edited_desc = st.text_area("설명", value=row.get('description', ''), key=f"edit_tc_desc_{row['id']}")
                                edited_link = st.text_input("링크", value=row.get('link', ''), key=f"edit_tc_link_{row['id']}")
                                
                                col1, col2 = st.columns(2)
                                with col1:
                                    if st.button("💾 저장", key=f"save_tc_{row['id']}", use_container_width=True):
                                        try:
                                            supabase.table(TABLE_NAME).update({
                                                'category': edited_category,
                                                'name': edited_name,
                                                'description': edited_desc,
                                                'link': edited_link
                                            }).eq('id', row['id']).execute()
                                            
                                            st.session_state.editing_test_case_id = None
                                            st.success("✅ 수정되었습니다!")
                                            st.rerun()
                                        except Exception as e:
                                            st.error(f"❌ 수정 실패: {str(e)}")
                                
                                with col2:
                                    if st.button("❌ 취소", key=f"cancel_tc_{row['id']}", use_container_width=True):
                                        st.session_state.editing_test_case_id = None
                                        st.rerun()
                            
                            else:
                                # 📖 보기 모드
                                st.write(f"**카테고리:** {row.get('category', '미분류')}")
                                st.write(f"**이름:** {row.get('name', '제목 없음')}")
                                if row.get('description'):
                                    st.write(f"**설명:** {row['description']}")
                                if row.get('link'):
                                    st.write(f"**링크:** {row['link']}")
                                
                                # data 컬럼 표시
                                if tc_data:
                                    with st.expander("📋 상세 데이터", expanded=False):
                                        st.json(tc_data)
                                
                                col1, col2 = st.columns(2)
                                
                                # 수정 버튼
                                with col1:
                                    if st.button("✏️ 수정", key=f"edit_tc_{row['id']}", use_container_width=True):
                                        st.session_state.editing_test_case_id = row['id']
                                        st.rerun()
                                
                                # 삭제 버튼
                                with col2:
                                    if st.button("🗑️ 삭제", key=f"delete_tc_{row['id']}", use_container_width=True):
                                        try:
                                            # 1. DB에서 삭제
                                            supabase.table(TABLE_NAME).delete().eq('id', row['id']).execute()

                                            # 2. 캐시 클리어
                                            st.cache_data.clear()
                                            
                                            # 3. 카운트 업데이트
                                            supabase = get_supabase_client()
                                            if supabase:
                                                result = supabase.table(TABLE_NAME).select('id', count='exact').execute()
                                                st.session_state.tc_count = result.count  # count 사용
                                            
                                            st.success("✅ 삭제되었습니다!")
                                            st.rerun()

                                        except Exception as e:
                                            st.error(f"❌ 삭제 실패: {str(e)}")

            else:
                st.info("아직 저장된 테스트 케이스가 없습니다.")

        except Exception as e:
            st.error(f"❌ 조회 실패: {str(e)}")
    else:
        st.error("❌ Supabase 연결 실패")

# 기획 문서 전체보기 페이지
elif page == "spec_docs":
    st.header("📚 전체 기획 문서")
    
    # 홈으로 돌아가기 링크
    st.markdown(f'<a href="/" target="_self">🏠 홈으로 돌아가기</a>', unsafe_allow_html=True)
    st.markdown("---")
    
    # Supabase에서 직접 로드
    supabase = get_supabase_client()
    if supabase:
        try:
            # 1. 전체 개수 조회
            count_result = supabase.table(SPEC_TABLE_NAME).select('id', count='exact').execute()
            total_count = count_result.count

            st.metric("전체 문서 수", f"{total_count}개")
            
            # 2. 최근 2개만 조회
            result = supabase.table(SPEC_TABLE_NAME)\
                .select('*')\
                .order('id', desc=True)\
                .limit(2)\
                .execute()

            if result.data:
                st.markdown("### 📌 최근 등록한 기획 문서 (2개)")
                st.markdown("---")

                # 전체 기획 문서 표시
                for row in result.data:
                    with st.expander(f"[{row.get('doc_type', '기타')}] {row.get('title', '제목 없음')}", expanded=False):

                        is_editing = st.session_state.editing_spec_doc_id == row['id']

                        if is_editing:
                            edited_title = st.text_input("문서 제목", value=row.get('title', ''), key=f"edit_spec_title_{row['id']}")
                            edited_type = st.selectbox("문서 유형", ["Notion", "Jira", "기타"], 
                                                       index=["Notion", "Jira", "기타"].index(row.get('doc_type', '기타')),
                                                       key=f"edit_spec_type_{row['id']}")
                            edited_link = st.text_input("링크", value=row.get('link', ''), key=f"edit_spec_link_{row['id']}")
                            edited_content = st.text_area("내용", value=row.get('content', ''), height=300, key=f"edit_spec_content_{row['id']}")

                            col1, col2 = st.columns(2)
                            with col1:
                                if st.button("💾 저장", key=f"save_spec_{row['id']}", use_container_width=True):
                                    try:
                                        supabase.table(SPEC_TABLE_NAME).update({
                                            'title': edited_title,
                                            'doc_type': edited_type,
                                            'link': edited_link,
                                            'content': edited_content
                                        }).eq('id', row['id']).execute()

                                        st.session_state.editing_spec_doc_id = None
                                        st.success("✅ 수정되었습니다!")
                                        st.rerun()
                                    except Exception as e:
                                        st.error(f"❌ 수정 실패: {str(e)}")

                            with col2:
                                if st.button("❌ 취소", key=f"cancel_spec_{row['id']}", use_container_width=True):
                                    st.session_state.editing_spec_doc_id = None
                                    st.rerun()

                        else:
                            st.write(f"**문서 유형:** {row.get('doc_type', '기타')}")
                            st.write(f"**링크:** {row.get('link', '')}")
                            st.write(f"**내용:**")
                            st.text(row.get('content', ''))


                            col1, col2 = st.columns(2)
                            with col1:
                                # 수정 버튼
                                if st.button("✏️ 수정", key=f"edit_spec_{row['id']}", use_container_width=True):
                                    st.session_state.editing_spec_doc_id = row['id']
                                    st.rerun()

                            with col2:
                                # 삭제 버튼
                                if st.button("🗑️ 삭제", key=f"delete_spec_{row['id']}", use_container_width=True):
                                    try:
                                        # 1. DB에서 삭제
                                        supabase.table(SPEC_TABLE_NAME).delete().eq('id', row['id']).execute()

                                        # 2. 캐시 클리어
                                        st.cache_data.clear()

                                        # 3. 카운트 업데이트
                                        result = supabase.table(SPEC_TABLE_NAME).select('id', count='exact').execute()
                                        st.session_state.doc_count = result.count  # count 사용
                                        
                                        st.success("✅ 삭제되었습니다!")
                                        st.rerun()
                                    except Exception as e:
                                        st.error(f"❌ 삭제 실패: {str(e)}")

            else:
                st.info("아직 저장된 기획 문서가 없습니다.")
                    
        except Exception as e:
            st.error(f"❌ 조회 실패: {str(e)}")

    else:
        st.error("❌ Supabase 연결 실패")


# ============================================
# 1. 테스트 케이스 추천 페이지
# ============================================
elif page == "recommend":
    st.header("📝 AI 테스트 케이스 추천")
    st.markdown('<a href="/" target="_self">🏠 홈으로 돌아가기</a>', unsafe_allow_html=True)
    st.markdown("---")
    
    col1, col2 = st.columns([2, 1])

    with col1:
        st.header("🔍 AI 기반 테스트 케이스 추천")

        # 세션 스테이트에서 가져오기
        tc_count = st.session_state.get('tc_count', 0)
        doc_count = st.session_state.get('doc_count', 0)

        if tc_count == 0 and doc_count == 0:
            st.warning("⚠️ 먼저 테스트 케이스나 기획 문서를 추가해주세요!")
            st.info("💡 왼쪽 사이드바에서 데이터를 추가할 수 있습니다.")
        else:
            st.info(f"📊 현재 **{tc_count}개**의 테스트 케이스와 **{doc_count}개**의 기획 문서를 학습할 수 있습니다.")

                
        search_query = st.text_area(
            "테스트하고 싶은 기능을 입력하세요.\n설명을 상세하게 적을수록 AI는 더 정확한 케이스를 찾아서 추천해줍니다!",
            placeholder="예: 상품별 구매평 연동 기능 QA\nBO 쇼핑 > 구매평 > 구매평 연동에 해당 기능이 추가될 예정\n테스트 케이스 30개 이상 만들어봐",
            height=150,
            key="search_input"
        )
            
        if st.button("AI 추천 받기", type="primary"):
            if search_query:
                with st.spinner("AI가 유사한 케이스를 검색중이에요. 1분 ~ 최대 5분 소요될 수 있어요🥹"):
                        # Gemini 클라이언트 직접 생성
                        api_key = os.environ.get("GOOGLE_API_KEY")
                        if not api_key:
                            st.error("❌ GOOGLE_API_KEY 환경 변수가 설정되지 않았습니다.")
                            st.stop()

                        genai.configure(api_key=api_key)
                    
                        # 벡터 유사도 검색
                        try:
                            # 1. Supabase에서 유사한 테스트 케이스 검색
                            with st.spinner("🔍 1단계: 벡터 검색 중..."):
                                relevant_cases = hybrid_search_test_cases(
                                    query_text=search_query,
                                    limit=50,
                                    similarity_threshold=0.3  # 30% 이상 유사도
                                )

                                # 세션 스테이트에 저장
                                st.session_state.relevant_cases = relevant_cases
                                
                            if relevant_cases:
                                st.success(f"✅ 1단계 완료: {len(relevant_cases)}개 발견")

                                # 유사도 정보 표시
                                with st.expander("🔍 검색된 케이스 미리보기", expanded=False):
                                    for idx, tc in enumerate(relevant_cases[:5], 1):  # 상위 5개만
                                        similarity = tc.get('similarity', 0)
                                        st.write(f"{idx}. **{tc.get('name')}** (유사도: {similarity:.2%})")

                            else:
                                st.warning("⚠️ 유사한 테스트 케이스를 찾지 못했습니다. 일반 케이스로 진행합니다.")
                                # 벡터 검색 실패 시에도 하이브리드 검색 사용 (임계값 낮춤)
                                all_cases = hybrid_search_test_cases(
                                    query_text=search_query,
                                    category_filter=None
                                )
                                
                                # 세션 스테이트에 저장
                                st.session_state.relevant_cases = all_cases

                            # 2. 기획 문서도 벡터 검색
                            spec_docs_str = ""
                            spec_docs = hybrid_search_spec_docs(query_text=search_query)

                            if spec_docs:
                                st.info(f"📚 {len(spec_docs)}개의 관련 기획 문서를 발견했습니다!")
                                spec_docs_str = "\n\n=== 관련 기획 문서 ===\n"
                                for doc in spec_docs:
                                    spec_docs_str += f"\n[문서 제목: {doc['title']}]\n[문서 유형: {doc['doc_type']}]\n[유사도: {doc.get('similarity', 0):.2%}]\n[내용]\n{doc['content'][:500]}...\n\n---\n"

                            # 3. AI 프롬프트용 데이터 준비
                            test_cases_str = json.dumps(
                                [
                                    {
                                        "id": tc.get("id"),
                                        "category": tc.get("category"),
                                        "name": tc.get("name"),
                                        "description": tc.get("description"),
                                        "data": tc.get("data"),
                                        "similarity": tc.get("similarity")
                                    }
                                    for tc in relevant_cases
                                ],
                                ensure_ascii=False,
                                indent=2
                            )
                            
                        except Exception as e:
                            st.error(f"❌ 하이브리드 검색 실패: {str(e)}")
                            st.warning("최소 임계값으로 재시도합니다...")

                            try:
                                # 임계값 0으로 하이브리드 검색 재시도
                                relevant_cases = hybrid_search_test_cases(
                                    query_text=search_query,
                                    category_filter=None
                                )

                                if relevant_cases:
                                    test_cases_str = json.dumps(
                                        [
                                            {
                                                "id": tc.get("id"),
                                                "category": tc.get("category"),
                                                "name": tc.get("name"),
                                                "description": tc.get("description"),
                                                "data": tc.get("data"),
                                                "similarity": tc.get("similarity")
                                            }
                                            for tc in relevant_cases
                                        ],
                                        ensure_ascii=False,
                                        indent=2
                                    )
                                    st.session_state.relevant_cases = relevant_cases
                                    st.info(f"✅ {len(relevant_cases)}개의 테스트 케이스를 찾았습니다 (재시도 성공)")
                                else:
                                    st.warning("재시도에도 결과를 찾지 못했습니다. 일반 케이스로 진행합니다.")
                                    relevant_cases = []
                                    test_cases_str = "[]"
                                    st.session_state.relevant_cases = []
            
                            except Exception as e2:
                                st.error(f"❌ 재시도 실패: {str(e2)}")
                                relevant_cases = []
                                test_cases_str = "[]"
                                st.session_state.relevant_cases = []
    
                            spec_docs_str = ""

                            # 세션 스테이트에 저장
                            st.session_state.relevant_cases = relevant_cases
                        
                        # 4. AI 프롬프트 (기존과 동일)
                        prompt = f"""[역할 부여]
너는 나와 같이 IT 노코드 웹 빌더 SaaS에 다니고 있는 꼼꼼한 QA 전문가, QA 엔지니어야.
(1) 테스트 설계, 테스트 케이스 작성, 자동화 업무 수행
(3) 서비스 안정성 기여. 리그레이션을 중심 업무 수행

확실하지 않은 정보는 '추정' 또는 '불확실'하다고 명시하고, 최신 정보가 필요한 경우 그렇게 알려줘.
혹시나 실제 고객, 회원 이름이 들어간 문서가 있다면, 실제 이름 대신 'Customer A, B, C'를 사용해. 또는 '홍길동', '김영희'와 같은 가명을 사용해줘.
개인정보나 기밀 정보는 일반화하여 처리해.

[제품 정보]
1. IO: 서비스 메인 페이지. 서비스 이용자는 IO에서 회원가입, 로그인을 하고 본인 소유 사이트를 관리 등을 함.
2. BO: Back Office. 사이트 관리자가 접속해서 사이트를 관리하는 공간 (쇼핑몰 세팅, 예약 기능 세팅, 컨텐츠 관리 등). 관리자 페이지에서 '디자인 모드'에 접속할 수 있음.
3. DM: 디자인 모드(Design Mode). 사이트 관리자가 접속해서 사이트를 디자인하는 공간 (상품 상세페이지 디자인 설정, 메뉴 추가/삭제, 메뉴 안에 위젯 추가/삭제 등)
4. FO: Front Office. 실제 사이트 방문자(엔드유저)가 상품을 보고 구매하거나, 예약하거나, 게시글을 보는 곳

[요청]
"{search_query}"에 대한 테스트 케이스 작성

[학습 데이터]
다음은 현재 시스템에 등록된 테스트 케이스들입니다:
{test_cases_str}

{spec_docs_str}

[테스트 케이스 표 양식]
반드시 다음 양식을 따라서 테스트 케이스를 작성해줘:
| NO | CATEGORY | DEPTH 1 | DEPTH 2 | DEPTH 3 | PRE-CONDITION | STEP | EXPECT RESULT |

사용자의 요청을 분석하고, 다음을 수행할 것:
1. 사용자가 테스트하려는 기능과 **직접 관련된** 테스트 케이스를 찾을 것
2. 기획 문서를 참고하여 기능의 의도와 맥락을 파악할 것
3. 그 기능이 작동하기 위해 **의존하는 다른 기능**들을 추론할 것
4. 논리적인 순서로 테스트 체크리스트를 만들 것
5. **반드시 위 표 양식으로 신규 테스트 케이스들을 생성할 것. NO 1부터 번호 시작**
6. **existing_test_cases의 id는 반드시 숫자여야 함. 학습 데이터의 id 필드를 참조할 것**

응답 형식:
```json
{{
  "reasoning": "왜 이런 테스트 케이스들이 필요한지 단계별 추론 과정 (한국어로 설명)",
  "existing_test_cases": [
    {{
      "id": 테스트케이스 숫자 ID (예: 1, 2, 3),
      "reason": "이 기존 테스트가 왜 필요한지 간단한 설명"
    }}
  ],
  "new_test_cases": [
    {{
      "no": 번호,
      "category": "카테고리",
      "depth1": "대분류",
      "depth2": "중분류 또는 빈 문자열",
      "depth3": "소분류 또는 빈 문자열",
      "pre_condition": "사전조건 또는 빈 문자열",
      "step": "수행 단계",
      "expect_result": "예상 결과"
    }}
  ],
  "test_order": "추천하는 테스트 순서 설명",
  "additional_suggestions": "추가로 필요할 수 있는 테스트 제안(edge case)"
}}
```

중요: 
1. 반드시 JSON 형식으로만 응답
2. new_test_cases는 반드시 표 양식에 맞춰 작성
3. 벡터 검색으로 찾은 유사 케이스를 충분히 활용할 것
"""

                        # 5. AI 응답 처리
                        try:
                            # Gemini 직접 호출
                            api_key = os.environ.get("GOOGLE_API_KEY")
                            genai.configure(api_key=api_key)
                            model = genai.GenerativeModel('gemini-2.5-flash')
                            response = model.generate_content(prompt)
                            response_text = response.text
                                        
                            # JSON 파싱
                            if "```json" in response_text:
                                json_str = response_text.split("```json")[1].split("```")[0].strip()
                            else:
                                json_str = response_text.strip()

                            import re
                            json_str_cleaned = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]', '', json_str)

                            try:
                                ai_response = json.loads(json_str_cleaned)
                            except json.JSONDecodeError as e:
                                st.error(f"❌ JSON 파싱 오류: {str(e)}")

                                with st.expander("🔧 디버깅 정보 (개발자용)", expanded=False):
                                    st.write(f"**오류 위치:** line {e.lineno}, column {e.colno}")
                                    st.write(f"**오류 메시지:** {e.msg}")
                                    st.code(json_str_cleaned[:1000], language="json")

                                try:
                                    json_str_final = json_str_cleaned.replace('\n', ' ').replace('\r', ' ').replace('\t', ' ')
                                    json_str_final = re.sub(r'\s+', ' ', json_str_final)
                                    ai_response = json.loads(json_str_final)
                                    st.warning("⚠️ JSON 파싱에 문제가 있어 일부 데이터가 손실되었을 수 있습니다.")
                                except:
                                    st.error("❌ AI 응답을 처리할 수 없습니다. 다시 시도해주세요.")
                                    st.stop()

                            st.session_state.search_history.append({
                                "query": search_query,
                                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                "response": ai_response
                            })

                            st.session_state.last_ai_response = ai_response
                            st.success("✅ AI 분석이 완료되었습니다!")

                        except Exception as e:
                            st.error(f"❌ AI 분석 중 오류가 발생했습니다: {str(e)}")
            else:
                st.warning("검색어를 입력해주세요.")
                    

        # ✅ 버튼 클릭 블록 밖에서 세션 체크
        if 'last_ai_response' in st.session_state:
            ai_response = st.session_state.last_ai_response

            # 타입 체크 추가
            if not isinstance(ai_response, dict):
                st.error("❌ AI 응답 형식이 올바르지 않습니다. 다시 시도해주세요.")
                st.write(f"🔍 Debug: ai_response 타입 = {type(ai_response)}")
                st.write(f"🔍 Debug: ai_response 내용 = {ai_response}")

                # 세션 초기화
                if 'last_ai_response' in st.session_state:
                    del st.session_state.last_ai_response
                st.stop()

            st.markdown("### 🧠 AI의 사고 과정")
            st.info(ai_response.get("reasoning", "추론 과정 없음"))
            
            if ai_response.get("new_test_cases"):
                st.markdown("### AI가 생성한 신규 테스트 케이스")
                
                df_data = []
                for tc in ai_response.get("new_test_cases", []):
                    df_data.append({
                        "NO": tc.get("no", ""),
                        "CATEGORY": tc.get("category", ""),
                        "DEPTH 1": tc.get("depth1", ""),
                        "DEPTH 2": tc.get("depth2", ""),
                        "DEPTH 3": tc.get("depth3", ""),
                        "PRE-CONDITION": tc.get("pre_condition", ""),
                        "STEP": tc.get("step", ""),
                        "EXPECT RESULT": tc.get("expect_result", "")
                    })
                
                df = pd.DataFrame(df_data)
                
                st.dataframe(
                    df,
                    use_container_width=True,
                    hide_index=True
                )

                col1, col2 = st.columns(2)

                with col1:
                    if EXCEL_AVAILABLE:
                        output = BytesIO()
                        with pd.ExcelWriter(output, engine='openpyxl') as writer:
                            df.to_excel(writer, index=False, sheet_name='테스트케이스')
                            workbook = writer.book
                            worksheet = writer.sheets['테스트케이스']
                        
                            header_fill = PatternFill(start_color='4A90A4', end_color='4A90A4', fill_type='solid')
                            header_font = Font(bold=True, color='FFFFFF')
                            center_alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
                        
                            for cell in worksheet[1]:
                                cell.fill = header_fill
                                cell.font = header_font
                                cell.alignment = center_alignment
                        
                            column_widths = {'A': 5, 'B': 15, 'C': 15, 'D': 20, 'E': 20, 'F': 30, 'G': 40, 'H': 40}
                            for column, width in column_widths.items():
                                worksheet.column_dimensions[column].width = width
                    
                        output.seek(0)
                        st.download_button(
                            label="📥 테스트 케이스 Excel로 다운로드",
                            data=output,
                            file_name=f"test_cases_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            use_container_width=True
                        )

                # 학습 데이터로 저장 버튼
                with col2:
                    if st.button("💾 학습시키기", type="primary", use_container_width=True):
                        # AI가 생성한 테스트 케이스를 그룹으로 저장
                        group_id = f"ai_generated_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                        table_data = []
                        
                        for tc in ai_response.get("new_test_cases", []):
                            table_data.append({
                                'NO': str(tc.get("no", "")),
                                'CATEGORY': tc.get("category", ""),
                                'DEPTH 1': tc.get("depth1", ""),
                                'DEPTH 2': tc.get("depth2", ""),
                                'DEPTH 3': tc.get("depth3", ""),
                                'PRE-CONDITION': tc.get("pre_condition", ""),
                                'STEP': tc.get("step", ""),
                                'EXPECT RESULT': tc.get("expect_result", "")
                            })
                        
                        if table_data:
                            group_test = {
                                "group_id": group_id,
                                "input_type": "ai_generated_group",
                                "category": "AI 생성",
                                "name": f" ({len(table_data)}개)",
                                "table_data": table_data,
                            }

                            with st.spinner("저장 중..."):
                                saved_count = save_test_case_to_supabase(group_test)

                            if saved_count > 0:
                                # 1. 캐시 클리어
                                st.cache_data.clear()

                                # 2. DB 반영 대기
                                import time
                                time.sleep(0.5)

                                # 3. 저장 직후 카운트 업데이트
                                supabase = get_supabase_client()
                                if supabase:
                                    try:
                                        result = supabase.table(TABLE_NAME).select('id', count='exact').execute()
                                        new_count = result.count  # count 사용

                                        # 플래그 설정
                                        st.session_state.force_reload_tc_count = True
                                        st.session_state.tc_count = new_count

                                    except Exception as e:
                                        st.error(f"카운트 업데이트 실패: {str(e)}")

                                st.success(f"✅ {saved_count}개 저장 완료!")
                                del st.session_state.last_ai_response
                                st.rerun()

            if ai_response.get("test_order"):
                st.markdown("### 🔄 권장 테스트 순서")
                st.write(ai_response["test_order"])
            
            if ai_response.get("additional_suggestions"):
                st.markdown("### 💡 추가 제안 (Edge Cases)")
                st.warning(ai_response["additional_suggestions"])

            if ai_response.get("existing_test_cases"):
                st.markdown("### 📝 기존 테스트 케이스 활용")

                # 세션 스테이트에서 relevant_cases 가져오기
                relevant_cases = st.session_state.get('relevant_cases', [])

                # relevant_cases가 없으면 경고 표시
                if not relevant_cases:
                    st.warning("⚠️ 검색 결과를 찾을 수 없습니다. 다시 검색해주세요.")
                else:
                    # 최초 접힘 상태로 변경
                    with st.expander("기존 테스트 케이스 목록", expanded=False):
                        for i, rec in enumerate(ai_response.get("existing_test_cases", []), 1):
                            # test_case = next((tc for tc in st.session_state.test_cases if tc["id"] == rec["id"]), None)
                            # relevant_cases에서 찾기 (session_state 대체)
                            # test_case = next((tc for tc in relevant_cases if tc.get("id") == rec.get("id")), None)

                            # id로 먼저 매칭 시도 (숫자 ID)
                            rec_id = rec.get("id")
                            test_case = None

                            # Case 1: rec_id가 숫자(정상)인 경우
                            if isinstance(rec_id, int):
                                test_case = next((tc for tc in relevant_cases if tc.get("id") == rec_id), None)

                            # Case 2: rec_id가 문자열(AI가 name을 반환)인 경우
                            if not test_case and isinstance(rec_id, str):
                                test_case = next((tc for tc in relevant_cases if tc.get("name") == rec_id), None)

                            # Case 3: 여전히 못 찾으면 name으로 시도
                            if not test_case:
                                test_case = next((tc for tc in relevant_cases if tc.get("name") and rec_id and tc.get("name") in str(rec_id)), None)
                        
                        
                            if test_case:
                                with st.expander(f"✓ {i}. [{test_case.get('category', '미분류')}] {test_case.get('name', '제목 없음')}", expanded=False):
                                    st.markdown(f"**왜 필요한가?** {rec.get('reason', '')}")

                                    # table_data가 있으면 표시
                                    if test_case.get('table_data'):
                                        st.markdown("**테스트 케이스 표:**")
                                        df_tc = pd.DataFrame([{
                                            'NO': item.get('NO', ''),
                                            'CATEGORY': item.get('CATEGORY', ''),
                                            'DEPTH 1': item.get('DEPTH 1', ''),
                                            'DEPTH 2': item.get('DEPTH 2', ''),
                                            'DEPTH 3': item.get('DEPTH 3', ''),
                                            'STEP': item.get('STEP', ''),
                                            'EXPECT RESULT': item.get('EXPECT RESULT', '')
                                        } for item in [test_case.get('table_data')] if isinstance(test_case.get('table_data'), dict)])
                                        st.dataframe(df_tc, use_container_width=True, hide_index=True)
                                    else:
                                        st.markdown(f"**설명:** {test_case.get('description', '')}")
                            else:
                                st.warning(f"⚠️ 케이스 ID {rec.get('id')}를 찾을 수 없습니다.")


    with col2:
        st.header("📊 검색 히스토리")
        
        if st.session_state.search_history:
            for i, history in enumerate(reversed(st.session_state.search_history[-5:]), 1):
                # ✅ 안전한 접근 - history가 None이거나 dict가 아니면 스킵
                if not history or not isinstance(history, dict):
                    continue
                    
                # ✅ 필수 키 확인
                timestamp = history.get('timestamp', '알 수 없음')
                query = history.get('query', '검색어 없음')

                with st.expander(f"{timestamp[:10]} - {query[:20]}...", expanded=(i==1)):
                    st.write(f"**검색어:** {query}")

                    # ✅ response 안전한 접근
                    if history.get('response') and isinstance(history['response'], dict):
                        existing_count = len(history['response'].get('existing_test_cases', []))
                        new_count = len(history['response'].get('new_test_cases', []))
                        st.write(f"**기존 테스트:** {existing_count}개")
                        st.write(f"**신규 생성:** {new_count}개")
                    else:
                        st.warning("⚠️ 이 검색은 오류가 발생했습니다.")
        else:
            st.info("아직 검색 히스토리가 없습니다.")

    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown("<br>", unsafe_allow_html=True)         
            

    # 하단 정보
    st.markdown("---")
    st.markdown("""
    #### 💡 사용 방법
    1. **학습 데이터 추가 (사이드바. QA팀 전용)**
       - 📝 테스트 케이스: 기존 테스트 케이스를 표, 자유 형식, CSV/Excel로 추가
       - 📚 기획 문서: 노션, Jira 문서를 복사해서 추가
       - ☁️ **Supabase에 자동 저장돼요**
    2. **검색창**에 테스트하고 싶은 기능을 입력!
       - **AI가 자동으로** 기존 데이터를 학습하여 신규 테스트 케이스를 생성해요
       - 생성된 테스트 케이스(표)는 Excel로 다운로드할 수 있어요
    """)




# 사전 리스크 확인 페이지
elif page == "risk":
    st.header("⚠️ 사전 리스크 확인")
    st.markdown('<a href="/" target="_self">🏠 홈으로 돌아가기</a>', unsafe_allow_html=True)
    st.markdown("---")

    st.info("💡 추가/수정할 기능을 입력하면, AI가 발생 가능한 리스크와 사이드 이펙트를 분석해줍니다.")

    # 입력 영역
    feature_description = st.text_area(
        "기능 설명을 입력하세요",
        placeholder="예시:\n정기 발행 쿠폰 기능이 추가될 예정입니다.\n- 정기 발행 쿠폰 템플릿 생성 -> 매월 오전 7시에 지정 발행 쿠폰으로 발행됨",
        height=200,
        key="risk_input"
    )

    if st.button("⚠️ 리스크 검토 시작", type="primary"):
        if not feature_description:
            st.warning("⚠️ 기능 설명을 입력해주세요!")
        else:
            with st.spinner("AI가 리스크를 분석 중입니다..."):
                # 1. 관련 테스트 케이스 검색
                relevant_cases = hybrid_search_test_cases(
                    query_text=feature_description,
                    limit=30,
                    similarity_threshold=0.3
                )

                # 2. 관련 기획 문서 검색
                spec_docs = hybrid_search_spec_docs(
                    query_text=feature_description,
                    limit=10
                )

                # 3. AI 프롬프트 생성
                test_cases_str = json.dumps(
                    [{"id": tc.get("id"), "name": tc.get("name"), "description": tc.get("description")}
                     for tc in relevant_cases],
                    ensure_ascii=False
                )

                spec_docs_str = ""
                if spec_docs:
                    spec_docs_str = "\n\n=== 관련 기획 문서 ===\n"
                    for doc in spec_docs:
                        spec_docs_str += f"\n[{doc['title']}]\n{doc['content'][:300]}...\n"

                prompt = f"""
[역할]
너는 IT SaaS 전문가로, 사전 리스크 검토를 담당한다.

[요청]
다음 기능에 대해 발생 가능한 리스크와 사이드 이펙트를 분석해줘:
{feature_description}

[학습 데이터]
{test_cases_str}
{spec_docs_str}

[분석 항목]
1. **직접적인 리스크**: 이 기능 자체에서 발생할 수 있는 문제
2. **연쇄 리스크**: 이 기능이 영향을 줄 수 있는 다른 기능들
3. **사이드 이펙트**: 예상치 못한 부작용
4. **(참고) 테스트 권장 사항**: 어떤 부분을 집중적으로 테스트해야 하는지

응답 형식 (JSON):
```json
{{
  "direct_risks": ["리스크1", "리스크2", ...],
  "chain_risks": ["연쇄 리스크1", "연쇄 리스크2", ...],
  "side_effects": ["사이드 이펙트1", "사이드 이펙트2", ...],
  "test_recommendations": ["테스트 권장1", "테스트 권장2", ...],
  "overall_risk_level": "높음/중간/낮음"
}}
```
"""

                # 4. AI 호출
                try:
                    genai.configure(api_key=GOOGLE_API_KEY)
                    # model = genai.GenerativeModel('gemini-2.0-flash-exp')
                    model = genai.GenerativeModel('gemini-2.5-flash')
                    response = model.generate_content(prompt)
                    response_text = response.text

                    # JSON 파싱
                    if "```json" in response_text:
                        json_str = response_text.split("```json")[1].split("```")[0].strip()
                    else:
                        json_str = response_text.strip()

                    import re
                    json_str_cleaned = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]', '', json_str)

                    try:
                        risk_result = json.loads(json_str_cleaned)
                    except json.JSONDecodeError as e:
                        st.error(f"❌ JSON 파싱 오류: {str(e)}")
                        # 재시도
                        try:
                            json_str_final = json_str_cleaned.replace('\n', ' ').replace('\r', ' ').replace('\t', ' ')
                            json_str_final = re.sub(r'\s+', ' ', json_str_final)
                            risk_result = json.loads(json_str_final)
                            st.warning("⚠️ JSON 파싱에 문제가 있었지만 복구했습니다.")
                        except:
                            st.error("❌ AI 응답을 처리할 수 없습니다. 다시 시도해주세요.")
                            st.stop()

                    risk_result = json.loads(json_str)

                    # 5. 결과 표시
                    st.success("✅ 리스크 분석 완료!")

                    # 위험도 표시
                    risk_level = risk_result.get("overall_risk_level", "중간")
                    if risk_level == "높음":
                        st.error(f"🔴 **전체 위험도: {risk_level}**")
                    elif risk_level == "중간":
                        st.warning(f"🟡 **전체 위험도: {risk_level}**")
                    else:
                        st.info(f"🟢 **전체 위험도: {risk_level}**")

                    # 직접적인 리스크
                    with st.expander("⚠️ 직접적인 리스크", expanded=True):
                        for risk in risk_result.get("direct_risks", []):
                            st.warning(f"- {risk}")

                    # 연쇄 리스크
                    with st.expander("🔗 연쇄 리스크 (다른 기능 영향)", expanded=True):
                        for risk in risk_result.get("chain_risks", []):
                            st.info(f"- {risk}")

                    # 사이드 이펙트
                    with st.expander("💥 사이드 이펙트", expanded=True):
                        for effect in risk_result.get("side_effects", []):
                            st.error(f"- {effect}")

                    # 테스트 권장 사항
                    with st.expander("✅ (참고) 테스트 권장 사항", expanded=True):
                        for rec in risk_result.get("test_recommendations", []):
                            st.success(f"- {rec}")

                except Exception as e:
                    st.error(f"❌ 분석 실패: {str(e)}")


# 의도된 동작 확인 페이지
elif page == "verify":
    st.header("✅ 의도된 동작인지 확인")
    st.markdown('<a href="/" target="_self">🏠 홈으로 돌아가기</a>', unsafe_allow_html=True)
    st.markdown("---")

    st.info("💡 특정 동작이 버그인지 의도된 것인지 학습 데이터를 기반으로 판단합니다. (추론 없이 데이터만 사용)")

    # 입력 영역
    behavior_description = st.text_area(
        "확인하고 싶은 동작을 입력하세요",
        placeholder="예시:\n쿠폰 사용 시 적립금도 함께 사용할 수 있는 것 같은데, 이게 맞나요?\n아니면 쿠폰과 적립금은 동시 사용이 불가능한가요?",
        height=200,
        key="verify_input"
    )

    if st.button("✅ 동작 확인", type="primary"):
        if not behavior_description:
            st.warning("⚠️ 확인하고 싶은 동작을 입력해주세요!")
        else:
            with st.spinner("학습 데이터에서 확인 중..."):
                # 1. 관련 케이스 검색 (limit 없음)
                relevant_cases = hybrid_search_test_cases(
                    query_text=behavior_description,
                )

                # 2. 관련 문서 검색
                spec_docs = hybrid_search_spec_docs(
                    query_text=behavior_description,
                )

                if not relevant_cases and not spec_docs:
                    st.warning("⚠️ 학습 데이터에서 관련 정보를 찾을 수 없습니다.")
                else:
                    # 검색 결과 수 표시
                    st.info(f"📊 검색 결과: 테스트 케이스 {len(relevant_cases)}개, 기획 문서 {len(spec_docs)}개")
                    
                    # 3. AI 프롬프트 (추론 금지!)
                    test_cases_str = json.dumps(
                        [{"name": tc.get("name"), "description": tc.get("description"), 
                          "data": tc.get("data")} for tc in relevant_cases],
                        ensure_ascii=False
                    )

                    spec_docs_str = ""
                    if spec_docs:
                        spec_docs_str = "\n\n=== 기획 문서 ===\n"
                        for doc in spec_docs:
                            spec_docs_str += f"\n[{doc['title']}]\n{doc['content']}\n"

                    prompt = f"""
[역할]
너는 QA 전문가로, 학습 데이터만을 근거로 동작을 판단한다.

**중요: 절대 추론하지 마. 학습 데이터에 명시된 내용만 사용해.**

[질문]
{behavior_description}

[학습 데이터]
{test_cases_str}
{spec_docs_str}

[지침]
1. 학습 데이터에 **기록된 내용**만 사용
2. 학습 데이터에 없으면 "데이터 없음"이라고 답변
3. 추론, 추측, 일반적인 지식 사용 금지

응답 형식 (JSON):
```json
{{
  "found_in_data": true/false,
  "answer": "의도된 동작입니다" 또는 "버그일 가능성이 높습니다" 또는 "학습 데이터에 정보 없음",
  "evidence": "학습 데이터의 근거 (구체적인 인용)",
  "confidence": "높음/중간/낮음"
}}
```
"""

                    # 4. AI 호출
                    try:
                        genai.configure(api_key=GOOGLE_API_KEY)
                        # model = genai.GenerativeModel('gemini-2.0-flash-exp')
                        model = genai.GenerativeModel('gemini-2.5-flash')
                        response = model.generate_content(prompt)
                        response_text = response.text

                        # JSON 파싱
                        if "```json" in response_text:
                            json_str = response_text.split("```json")[1].split("```")[0].strip()
                        else:
                            json_str = response_text.strip()

                        import re
                        json_str_cleaned = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]', '', json_str)

                        try:
                            verify_result = json.loads(json_str_cleaned)
                        except json.JSONDecodeError as e:
                            st.error(f"❌ JSON 파싱 오류: {str(e)}")
                            try:
                                json_str_final = json_str_cleaned.replace('\n', ' ').replace('\r', ' ').replace('\t', ' ')
                                json_str_final = re.sub(r'\s+', ' ', json_str_final)
                                verify_result = json.loads(json_str_final)
                                st.warning("⚠️ JSON 파싱에 문제가 있었지만 복구했습니다.")
                            except:
                                st.error("❌ AI 응답을 처리할 수 없습니다. 다시 시도해주세요.")
                                st.stop()

                        verify_result = json.loads(json_str)

                        # 5. 결과 표시
                        found = verify_result.get("found_in_data", False)
                        answer = verify_result.get("answer", "")
                        evidence = verify_result.get("evidence", "")
                        confidence = verify_result.get("confidence", "중간")

                        if not found:
                            st.warning("⚠️ 학습 데이터에서 관련 정보를 찾지 못했습니다.")
                            st.info("💡 관련 부서에 문의하는 것을 권장합니다.")
                        else:
                            if "의도된" in answer:
                                st.success(f"✅ {answer}")
                            elif "버그" in answer:
                                st.error(f"⚠️ {answer}")
                            else:
                                st.info(f"ℹ️ {answer}")

                            st.markdown(f"**신뢰도**: {confidence}")
                            
                            with st.expander("📋 근거 데이터", expanded=True):
                                st.write(evidence)

                    except Exception as e:
                        st.error(f"❌ 확인 실패: {str(e)}")

# 키워드 검색 페이지
elif page == "keyword":
    st.header("🔍 키워드 검색")
    st.markdown('<a href="/" target="_self">🏠 홈으로 돌아가기</a>', unsafe_allow_html=True)
    st.markdown("---")

    st.info("💡 학습 데이터에서 키워드를 빠르게 검색합니다. (AI 사용 안 함)")

    # 검색 입력
    keyword = st.text_input(
        "검색 키워드",
        placeholder="예: 쿠폰, 결제, 배송",
        key="keyword_input"
    )

    # 검색 대상 선택
    search_target = st.radio(
        "검색 대상",
        ["테스트 케이스", "기획 문서", "전체"],
        horizontal=True
    )

    if st.button("🔍 검색", type="primary"):
        if not keyword:
            st.warning("⚠️ 검색 키워드를 입력해주세요!")
        else:
            supabase = get_supabase_client()
            if not supabase:
                st.error("❌ Supabase 연결 실패")
            else:
                with st.spinner(f"'{keyword}' 검색 중..."):
                    results_tc = []
                    results_doc = []

                    # 테스트 케이스 검색 (limit 없음)
                    if search_target in ["테스트 케이스", "전체"]:
                        try:
                            # ILIKE는 대소문자 구분 없는 LIKE
                            result = supabase.table(TABLE_NAME)\
                                .select('*')\
                                .or_(f"name.ilike.%{keyword}%,description.ilike.%{keyword}%,category.ilike.%{keyword}%")\
                                .execute()
                            results_tc = result.data
                        except Exception as e:
                            st.error(f"테스트 케이스 검색 오류: {str(e)}")

                    # 기획 문서 검색
                    if search_target in ["기획 문서", "전체"]:
                        try:
                            result = supabase.table(SPEC_TABLE_NAME)\
                                .select('*')\
                                .or_(f"title.ilike.%{keyword}%,content.ilike.%{keyword}%")\
                                .execute()
                            results_doc = result.data
                        except Exception as e:
                            st.error(f"기획 문서 검색 오류: {str(e)}")

                    # 결과 표시
                    total_count = len(results_tc) + len(results_doc)
                    
                    if total_count == 0:
                        st.warning(f"⚠️ '{keyword}' 검색 결과가 없습니다.")
                    else:
                        st.success(f"✅ 총 {total_count}개 발견")

                        # 테스트 케이스 결과
                        if results_tc:
                            st.markdown(f"### 📝 테스트 케이스 ({len(results_tc)}개)")
                            for tc in results_tc:  # 전체 표시
                                with st.expander(f"[{tc.get('category', '미분류')}] {tc.get('name', '제목 없음')}"):
                                    st.write(f"**설명**: {tc.get('description', '')}")
                                    if tc.get('link'):
                                        st.write(f"**링크**: {tc.get('link')}")

                        # 기획 문서 결과
                        if results_doc:
                            st.markdown(f"### 📚 기획 문서 ({len(results_doc)}개)")
                            for doc in results_doc:  # 전체 표시
                                with st.expander(f"[{doc.get('doc_type', '기타')}] {doc.get('title', '제목 없음')}"):
                                    st.write(f"**내용**: {doc.get('content', '')[:300]}...")
                                    if doc.get('link'):
                                        st.write(f"**링크**: {doc.get('link')}")

# 메인 페이지
else:
    # 사이드바
    with st.sidebar:
        st.header("🙌 WELCOME")

        # 연결 상태 표시
        if get_supabase_client():
            st.success("☁️ Supabase 연결됨")
        else:
            st.error("❌ Supabase 연결 실패")

        # 추가: 하이브리드 검색 설정 표시
        with st.expander("⚙️ 검색 설정", expanded=False):
            st.info(f"""
            **검색 방식**: {RERANK_METHOD.upper()}  
            **1차 검색**: {INITIAL_SEARCH_COUNT}개
            **최종 선택**: {FINAL_SEARCH_COUNT}개
            """)

        st.markdown("---")
        
        # 탭으로 구분
        tab1, tab2 = st.tabs(["📝 테스트 케이스", "📚 기획 문서"])
        
        # ============================================
        # 📝 탭 1: 테스트 케이스 추가
        # ============================================
        with tab1:
            with st.expander("➕ [QA팀 전용 버튼]\n테스트 케이스 추가", expanded=False):
                st.markdown("### 📝 테스트 케이스 입력")
                st.info("💡 3가지 방법 중 편한 방식으로 테스트 케이스를 추가하세요!")
                
                # 세션 스테이트에 편집용 데이터프레임 초기화
                if 'edit_df' not in st.session_state:
                    st.session_state.edit_df = pd.DataFrame({
                        'NO': [''],
                        'CATEGORY': [''],
                        'DEPTH 1': [''],
                        'DEPTH 2': [''],
                        'DEPTH 3': [''],
                        'PRE-CONDITION': [''],
                        'STEP': [''],
                        'EXPECT RESULT': ['']
                    })
                
                # ========== 방법 1: 표 형식 입력 ==========
                st.markdown("**방법 1: 표에서 직접 입력/편집**")
                
                # 행 추가/삭제 버튼
                col1, col2 = st.columns([1, 1])
                with col1:
                    if st.button("➕ 행 추가", key="add_row_tc"):
                        new_row = pd.DataFrame({
                            'NO': [''],
                            'CATEGORY': [''],
                            'DEPTH 1': [''],
                            'DEPTH 2': [''],
                            'DEPTH 3': [''],
                            'PRE-CONDITION': [''],
                            'STEP': [''],
                            'EXPECT RESULT': ['']
                        })
                        st.session_state.edit_df = pd.concat([st.session_state.edit_df, new_row], ignore_index=True)
                        st.rerun()
                
                with col2:
                    if st.button("🗑️ 모두 지우기", key="clear_tc"):
                        st.session_state.edit_df = pd.DataFrame({
                            'NO': [''],
                            'CATEGORY': [''],
                            'DEPTH 1': [''],
                            'DEPTH 2': [''],
                            'DEPTH 3': [''],
                            'PRE-CONDITION': [''],
                            'STEP': [''],
                            'EXPECT RESULT': ['']
                        })
                        st.rerun()

                # 데이터 에디터를 위한 고유 키 생성
                if 'editor_key' not in st.session_state:
                    st.session_state.editor_key = 0
                
                # 데이터 에디터 표시
                edited_df = st.data_editor(
                    st.session_state.edit_df,
                    use_container_width=True,
                    num_rows="dynamic",
                    hide_index=True,
                    column_config={
                        "NO": st.column_config.TextColumn("NO", width="small", help="번호"),
                        "CATEGORY": st.column_config.TextColumn("CATEGORY", width="medium", help="카테고리 (필수)"),
                        "DEPTH 1": st.column_config.TextColumn("DEPTH 1", width="medium", help="대분류 (필수)"),
                        "DEPTH 2": st.column_config.TextColumn("DEPTH 2", width="medium", help="중분류 (선택)"),
                        "DEPTH 3": st.column_config.TextColumn("DEPTH 3", width="medium", help="소분류 (선택)"),
                        "PRE-CONDITION": st.column_config.TextColumn("PRE-CONDITION", width="large", help="사전 조건 (선택)"),
                        "STEP": st.column_config.TextColumn("STEP", width="large", help="수행 단계"),
                        "EXPECT RESULT": st.column_config.TextColumn("EXPECT RESULT", width="large", help="예상 결과"),
                    },
                    key=f"test_case_editor_{st.session_state.editor_key}"
                )
                # 변경사항 즉시 반영
                if not edited_df.equals(st.session_state.edit_df):
                    st.session_state.edit_df = edited_df.copy()
                    st.session_state.editor_key += 1
                    st.rerun()
                
                st.session_state.edit_df = edited_df
                
                # 표 형식 저장 버튼
                if st.button("💾 표 형식 저장", type="primary", disabled=(len(edited_df) == 0), key="save_table_tc"):
                    if len(edited_df) > 0:
                        # 그룹 ID 생성
                        group_id = f"table_group_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
                        # 표 데이터 준비
                        table_data = []
                        for index, row in edited_df.iterrows():
                            if pd.isna(row['CATEGORY']) or row['CATEGORY'] == '' or pd.isna(row['DEPTH 1']) or row['DEPTH 1'] == '':
                                continue
            
                            table_data.append({
                                'NO': str(row['NO']) if row['NO'] and str(row['NO']).strip() else '',
                                'CATEGORY': str(row['CATEGORY']),
                                'DEPTH 1': str(row['DEPTH 1']),
                                'DEPTH 2': str(row.get('DEPTH 2', '')),
                                'DEPTH 3': str(row.get('DEPTH 3', '')),
                                'PRE-CONDITION': str(row.get('PRE-CONDITION', '')),
                                'STEP': str(row.get('STEP', '')),
                                'EXPECT RESULT': str(row.get('EXPECT RESULT', ''))
                            })
        
                        if table_data:
                            # Supabase에 저장 (개별 케이스로 쪼갬!)
                            group_test = {
                                "group_id": group_id,
                                "input_type": "table_group",
                                "category": "입력 그룹",
                                "name": f"({len(table_data)}개)",
                                "table_data": table_data
                            }
            
                            with st.spinner(f"{len(table_data)}개 케이스 저장 중..."):
                                saved_count = save_test_case_to_supabase(group_test)
            
                            if saved_count > 0:
                                # 1. 캐시 클리어
                                st.cache_data.clear()

                                # 2. DB 반영 대기 (선택사항)
                                import time
                                time.sleep(0.5)
                                
                                # 3. 저장 직후 카운트 업데이트
                                supabase = get_supabase_client()
                                if supabase:
                                    try:
                                        result = supabase.table(TABLE_NAME).select('id', count='exact').execute()
                                        new_count = result.count  # count 사용

                                        # 플래그 설정 (rerun 후 초기화 트리거)
                                        st.session_state.force_reload_tc_count = True
                                        st.session_state.tc_count = new_count
                                    except Exception as e:
                                        st.error(f"카운트 업데이트 실패: {str(e)}")

                                # 세션 초기화 (데이터프레임 리셋)
                                st.session_state.edit_df = pd.DataFrame({
                                    'NO': [''],
                                    'CATEGORY': [''],
                                    'DEPTH 1': [''],
                                    'DEPTH 2': [''],
                                    'DEPTH 3': [''],
                                    'PRE-CONDITION': [''],
                                    'STEP': [''],
                                    'EXPECT RESULT': ['']
                                })
                                st.success(f"✅ {saved_count}개의 테스트 케이스가 Supabase에 저장되었습니다!")
                                st.rerun()
                            else:
                                st.error("❌ 저장 실패!")
                        else:
                            st.warning("유효한 테스트 케이스가 없습니다. CATEGORY와 DEPTH 1은 필수 항목입니다.")
                
                st.markdown("---")
                
                # ========== 방법 2: 줄글 형식 (자유 입력) ==========
                st.markdown("**방법 2: 줄글 형식 (자유 입력)**")
                st.info("💡 테스트 케이스를 자유롭게 작성하고 AI가 학습할 수 있도록 저장하세요!")

                # 세션 스테이트 초기값 설정
                if 'tab1_tc_free_title' not in st.session_state:
                    st.session_state.tab1_tc_free_title = ""
                if 'tab1_tc_free_link' not in st.session_state:
                    st.session_state.tab1_tc_free_link = ""
                if 'tab1_tc_free_content' not in st.session_state:
                    st.session_state.tab1_tc_free_content = ""
                if 'tab1_tc_free_category' not in st.session_state:
                    st.session_state.tab1_tc_free_category = ""

                # 초기화 플래그 체크 (이전 저장 후 rerun되면 초기화)
                if st.session_state.get('tab1_tc_reset_flag', False):
                    st.session_state.tab1_tc_free_title = ""
                    st.session_state.tab1_tc_free_link = ""
                    st.session_state.tab1_tc_free_content = ""
                    st.session_state.tab1_tc_free_category = ""
                    st.session_state.tab1_tc_reset_flag = False
                
                st.text_input(
                    "제목 *",
                    placeholder="예: 쿠폰 지정 발행 테스트 설계",
                    key="tab1_tc_free_title"
                )

                st.text_input(
                    "링크 URL",
                    placeholder="https://www.notion.so/imweb/...",
                    key="tab1_tc_free_link"
                )
                
                st.text_area(
                    "내용 *",
                    placeholder="테스트 설계 내용을 자유롭게 작성하세요.\n\n[예시]\n1. BO에서 쿠폰 생성\n2. 특정 회원에게 쿠폰 지정 발행\n3. FO에서 쿠폰 사용 가능 여부 확인\n...",
                    height=300,
                    key="tab1_tc_free_content"
                )
                
                st.text_input(
                    "카테고리 *",
                    placeholder="쿠폰",
                    key="tab1_tc_free_category"
                )
                
                # 저장 버튼 및 로직
                if st.button("💾 줄글 형식 저장", type="primary", key="tab1_save_free_form_tc"):
                    # 세션 스테이트에서 직접 값 가져오기
                    if not st.session_state.tab1_tc_free_title or not st.session_state.tab1_tc_free_content or not st.session_state.tab1_tc_free_category:
                        st.warning("⚠️ 모든 항목을 입력해주세요!")
                    else:
                        # 줄글 형식으로 저장
                        free_form_test = {
                            "category": st.session_state.tab1_tc_free_category if st.session_state.tab1_tc_free_category else "기타",
                            "name": st.session_state.tab1_tc_free_title,
                            "link": st.session_state.tab1_tc_free_link,
                            "description": st.session_state.tab1_tc_free_content,
                            "input_type": "free_form"
                        }
                        with st.spinner("저장 중..."):
                            saved_count = save_test_case_to_supabase(free_form_test)

                        if saved_count > 0:
                            # 1. 캐시 클리어
                            st.cache_data.clear()

                            # 2. DB 반영 대기
                            import time
                            time.sleep(0.5)
                            
                            # 저장 직후 카운트 업데이트
                            supabase = get_supabase_client()
                            if supabase:
                                try:
                                    result = supabase.table(TABLE_NAME).select('id', count='exact').execute()
                                    new_count = result.count  # count 사용

                                    # 플래그 설정
                                    st.session_state.force_reload_tc_count = True
                                    st.session_state.tc_count = new_count

                                except Exception as e:
                                    st.error(f"카운트 업데이트 실패: {str(e)}")
                            
                            # 초기화 플래그 설정 후 rerun
                            st.session_state.tab1_tc_reset_flag = True
                                    
                            st.success(f"✅ '{free_form_test['name']}' 테스트 케이스가 Supabase에 저장되었습니다!")
                            st.rerun()
                        else:
                            st.error("❌ 저장 실패!")

                st.markdown("---")
                
                # ========== 방법 3: CSV/Excel 파일 업로드 ==========
                st.markdown("**방법 3: CSV/Excel 파일 업로드**")
                uploaded_file = st.file_uploader("CSV 또는 Excel 파일 선택", type=['csv', 'xlsx'], key="upload_tc")
                
                if uploaded_file is not None:
                    try:
                        if uploaded_file.name.endswith('.csv'):
                            df = pd.read_csv(uploaded_file)
                        else:
                            df = pd.read_excel(uploaded_file)
                        
                        required_columns = ['NO', 'CATEGORY', 'DEPTH 1', 'DEPTH 2', 'DEPTH 3', 'PRE-CONDITION', 'STEP', 'EXPECT RESULT']
                        
                        if not all(col in df.columns for col in required_columns):
                            st.warning("컬럼명이 일치하지 않습니다. 데이터를 확인해주세요.")
                            st.dataframe(df.head())
                        else:
                            # st.session_state.edit_df = df[required_columns].fillna('')
                            
                            # 모든 컬럼을 문자열로 변환 후 빈 값 처리
                            st.session_state.edit_df = df[required_columns].astype(str).replace('nan', '').replace('None', '')
                            st.success(f"✅ {len(df)}개 행이 로드되었습니다!")
                            st.info("👆 방법 1 로 올라가 '💾 표 형식 저장' 버튼을 눌러주세요!")
                            
                    except Exception as e:
                        st.error(f"파일 읽기 오류: {str(e)}")

            st.markdown("<br>", unsafe_allow_html=True)
            st.markdown("<br>", unsafe_allow_html=True)
            st.markdown("<br>", unsafe_allow_html=True)
            st.markdown("<br>", unsafe_allow_html=True)
            st.markdown("<br>", unsafe_allow_html=True)
            
            # 테스트 케이스 요약
            st.subheader(f"📋 저장된 테스트 케이스")

            # 세션 스테이트 우선 사용
            if 'tc_count' in st.session_state:
                total_count = st.session_state.tc_count
            else:

                # Supabase에서 실시간 조회
                supabase = get_supabase_client()
                if supabase:
                    try:
                        # 전체 개수
                        result = supabase.table(TABLE_NAME).select('id', count='exact').execute()
                        total_count = result.count  # ✅ count 사용
                        st.session_state.tc_count = total_count
                    except Exception as e:
                        st.error(f"통계 조회 실패: {str(e)}")
                        total_count = 0

                else:
                    total_count = 0

            st.metric("Supabase 전체 케이스 수", f"{total_count}개")

            # 카테고리별 통계
            if total_count > 0:
                # 추가: 카테고리 통계 위해 필요시 다시 조회
                if 'tc_count' in st.session_state:
                    supabase = get_supabase_client()
                    if supabase:
                        result = supabase.table(TABLE_NAME).select('id, category, data').execute()
                        categories = {}
                        for row in result.data:
                            cat = row.get('category', '미분류')
                            categories[cat] = categories.get(cat, 0) + 1

                        with st.expander("📊 카테고리별 통계", expanded=False):
                            for cat, count in sorted(categories.items(), key=lambda x: x[1], reverse=True):
                                st.write(f"**{cat}**: {count}개")

            # 새 탭으로 열기 링크
            if total_count > 0:
                st.markdown(
                    '<a href="?page=test_cases" target="_blank" style="text-decoration: none;">'
                    '<button style="width: 100%; padding: 10px; background-color: #f0f2f6; border: 1px solid #d0d0d0; border-radius: 5px; cursor: pointer;">'
                    '📝 전체 테스트 케이스 보기 (새 탭) →'
                    '</button></a>',
                    unsafe_allow_html=True
                )

       
        # 개발자 도구
        with tab1:
            st.markdown("---")
            with st.expander("🔧 개발자 도구", expanded=False):
                if st.button("🔍 사용 가능한 Gemini 모델 확인"):
                    try:
                        api_key = os.environ.get("GOOGLE_API_KEY")
                        genai.configure(api_key=api_key)
                
                        models = genai.list_models()
                        st.write("### 사용 가능한 모델 목록:")
                        for model in models:
                            if 'generateContent' in model.supported_generation_methods:
                                st.write(f"✅ {model.name}")
                    except Exception as e:
                        st.error(f"오류: {str(e)}")
        
        # ============================================
        # 📚 탭 2: 기획 문서 추가
        # ============================================
        with tab2:
            with st.expander("➕ [QA팀 전용 버튼]\n기획 문서 추가", expanded=False):
                st.markdown("### 📄 기획 문서 입력")
                st.info("💡 노션, Jira에서 작성한 문서를 복사해서 붙여넣으세요.\nAI가 이 내용을 학습합니다!")

                # 세션 스테이트 초기값 설정
                if 'tab2_spec_title' not in st.session_state:
                    st.session_state.tab2_spec_title = ""
                if 'tab2_spec_type' not in st.session_state:
                    st.session_state.tab2_spec_type = "Notion"
                if 'tab2_spec_link' not in st.session_state:
                    st.session_state.tab2_spec_link = ""
                if 'tab2_spec_content' not in st.session_state:
                    st.session_state.tab2_spec_content = ""

                # 초기화 플래그 체크
                if st.session_state.get('tab2_spec_reset_flag', False):
                    st.session_state.tab2_spec_title = ""
                    st.session_state.tab2_spec_type = "Notion"
                    st.session_state.tab2_spec_link = ""
                    st.session_state.tab2_spec_content = ""
                    st.session_state.tab2_spec_reset_flag = False

                # 문서 제목
                st.text_input(
                    "문서 제목 *",
                    placeholder="예: 공동구매 기능 스펙 문서",
                    key="tab2_spec_title"
                )
                
                # 문서 유형
                st.selectbox(
                    "문서 유형 *",
                    ["Notion", "Jira", "기타"],
                    key="tab2_spec_type"
                )

                # 링크 URL
                st.text_input(
                    "링크 URL *",
                    placeholder="https://www.notion.so/imweb/...",
                    key="tab2_spec_link"
                )
                
                # 문서 내용
                st.text_area(
                    "문서 내용 *",
                    placeholder="기획 의도, 스펙, 요구사항 등을 자유롭게 붙여넣으세요.\n\n예:\n[기획 배경]\n현재 공동구매 기능은...\n\n[주요 기능]\n1. 브랜드 정보 입력 모달\n2. 캠페인 생성 기능\n...",
                    height=300,
                    key="tab2_spec_content"
                )
                
                # 저장 버튼
                if st.button("💾 기획 문서 저장", type="primary", key="tab2_save_spec"):
                    if not st.session_state.tab2_spec_title or not st.session_state.tab2_spec_type or not st.session_state.tab2_spec_link or not st.session_state.tab2_spec_content:
                        st.warning("⚠️ 모든 항목을 입력해주세요!")
                    else:
                        new_spec = {
                            "title": st.session_state.tab2_spec_title,
                            "doc_type": st.session_state.tab2_spec_type,
                            "link": st.session_state.tab2_spec_link,
                            "content": st.session_state.tab2_spec_content,
                        }
                        
                        with st.spinner("저장 중..."):
                            success = save_spec_doc_to_supabase(new_spec)

                        if success:
                            # 1. 캐시 클리어
                            st.cache_data.clear()

                            # 2. DB 반영 대기
                            import time
                            time.sleep(0.5)
                            
                            # 3. 저장 직후 카운트 업데이트 (강제)
                            supabase = get_supabase_client()
                            if supabase:
                                try:
                                    result = supabase.table(SPEC_TABLE_NAME).select('id', count='exact').execute()
                                    new_count = result.count  # count 사용

                                    # 플래그 설정
                                    st.session_state.force_reload_doc_count = True
                                    st.session_state.doc_count = new_count

                                except Exception as e:
                                    st.error(f"카운트 업데이트 실패: {str(e)}")
                                    
                            # 초기화 플래그 설정 후 rerun
                            st.session_state.tab2_spec_reset_flag = True
            
                            st.success(f"✅ 기획 문서가 Supabase에 저장되었습니다!")
                            st.rerun()

                        else:
                            st.error("❌ 저장 실패!")

            st.markdown("<br>", unsafe_allow_html=True)
            st.markdown("<br>", unsafe_allow_html=True)
            st.markdown("<br>", unsafe_allow_html=True)
            st.markdown("<br>", unsafe_allow_html=True)
            st.markdown("<br>", unsafe_allow_html=True)
            
            # 기획 문서 요약
            st.subheader(f"📄 저장된 기획 문서")

            # 세션 스테이트 우선 사용
            if 'doc_count' in st.session_state:
                total_count = st.session_state.doc_count

            else:
                # Supabase에서 조회
                supabase = get_supabase_client()
                if supabase:
                    try:
                        result = supabase.table(SPEC_TABLE_NAME).select('id, title, doc_type').execute()
                        total_count = len(result.data)
                        st.session_state.doc_count = total_count
                    except Exception as e:
                        st.error(f"문서 통계 조회 실패: {str(e)}")
                        total_count = 0
                else:
                    total_count = 0

            st.metric("전체 문서 수", f"{total_count}개")

            # 새 탭으로 열기 링크
            if total_count > 0:
                st.markdown(
                    '<a href="?page=spec_docs" target="_blank" style="text-decoration: none;">'
                    '<button style="width: 100%; padding: 10px; background-color: #f0f2f6; border: 1px solid #d0d0d0; border-radius: 5px; cursor: pointer;">'
                    '📚 전체 기획 문서 보기 (새 탭) →'
                    '</button></a>',
                    unsafe_allow_html=True
                )


    # ============================================
    # 메인 영역 - 기능 선택
    # ============================================
    st.header("🎯 어떤 기능을 사용하시겠어요?")
    st.markdown("---")
    
    # 4개 버튼을 2x2 그리드로 배치
    col1, col2 = st.columns(2)
    
    with col1:
        if st.button(
            "👾 테스트 케이스 추천받기",
            use_container_width=True,
            help="AI가 유사한 케이스를 찾아 테스트 케이스를 생성해줍니다"
        ):
            st.query_params.update({"page": "recommend"})
            st.rerun()

        if st.button(
            "🔍 키워드 검색",
            use_container_width=True,
            help="학습 데이터 안에서 키워드를 검색합니다"
        ):
            st.query_params.update({"page": "keyword"})
            st.rerun()

    with col2:
        if st.button(
            "⚠️ 사전 리스크 확인",
            use_container_width=True,
            help="AI가 리스크와 사이드 이펙트를 분석해줍니다"
        ):
            st.query_params.update({"page": "risk"})
            st.rerun()

        if st.button(
            "✅ 의도된 동작인지 확인",
            use_container_width=True,
            help="학습 데이터 기반으로 버그 가능성을 판단합니다"
        ):
            st.query_params.update({"page": "verify"})
            st.rerun()

    # 안내 메시지
    st.markdown("---")
    st.info("""
    💡 **기능 설명**
    - 📝 **테스트 케이스 추천**: AI가 유사 케이스를 찾아 신규 테스트 케이스 생성
    - ⚠️ **사전 리스크 확인**: 기능 추가/수정 시 발생 가능한 리스크 분석
    - ✅ **의도된 동작 확인**: 특정 동작이 버그인지 의도된 것인지 판단 (AI 추론X)
    - 🔍 **키워드 검색**: 학습 데이터에서 빠르게 검색
    """)

    # 통계 표시
    tc_count = st.session_state.get('tc_count', 0)
    doc_count = st.session_state.get('doc_count', 0)
    
    col_a, col_b, col_c = st.columns(3)
    with col_a:
        st.metric("📊 테스트 케이스", f"{tc_count}개")
    with col_b:
        st.metric("📚 기획 문서", f"{doc_count}개")
    with col_c:
        st.metric("🔍 검색 방식", RERANK_METHOD.upper())
