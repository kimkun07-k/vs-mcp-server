// vs-mcp-server 디버거 통합 테스트용 타겟
int x = 42;
int y = x + 8;
string msg = "hello from debugger";
Console.WriteLine($"x={x}, y={y}, msg={msg}");  // BP_LINE=5
Console.ReadLine();
