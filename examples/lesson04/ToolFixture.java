// Independently written teaching fixture; contains no company source or business data.
public class ToolFixture {
    public static void main(String[] args) throws Exception {
        String mode = args.length == 0 ? "pass" : args[0];
        if (mode.equals("sleep")) Thread.sleep(30_000);
        int[] values = {100, 101, 500, 501};
        boolean[] expected = {false, true, true, false};
        for (int i = 0; i < values.length; i++) {
            boolean actual = (mode.equals("fail") ? values[i] >= 100 : values[i] > 100)
                && values[i] <= 500;
            if (actual != expected[i]) {
                throw new AssertionError("value=" + values[i] + ", expected=" + expected[i] + ", actual=" + actual);
            }
            System.out.println("value=" + values[i] + ", matched=" + actual);
        }
        System.out.println("4 boundary checks passed");
    }
}
