// vs_error_list 통합 테스트용 — 의도적 에러 + 경고
#warning TEST_WARNING: 이 경고는 vs_error_list 테스트용입니다
int x = undefinedVar;   // CS0103 error: 정의되지 않은 변수
Console.WriteLine(x);
