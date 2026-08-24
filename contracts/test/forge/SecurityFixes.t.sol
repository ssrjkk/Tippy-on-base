// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// Regression tests for the 2026-08-24 security fixes:
///
///   #1 Signed-cast wrap in LMSR.buyCost/sellProceeds -> free giant buys.
///      Fixed with explicit bounds checks (LMSR.SharesTooLarge) plus a
///      market-level MAX_SUPPLY_PER_OUTCOME cap on every minting path.
///   #2 cancelExpired() paid the pro-rata refund to the CREATOR instead of
///      the shareholders. Fixed with a pull-based claimCancelled() flow;
///      the creator only ever receives the no-holders subsidy back and the
///      final rounding-dust sweep.
///
/// Requires vendored deps: bash scripts/setup_foundry.sh  (installs
/// openzeppelin, prb-math and forge-std), then `forge test --match-contract SecurityFixesTest`.

import {Test} from "forge-std/Test.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {OutcomeMarket} from "../../OutcomeMarket.sol";
import {LMSR} from "../../LMSR.sol";

/// @dev Minimal USDC stand-in (SafeERC20-compatible bool returns).
contract FakeUSDC is IERC20 {
    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;
    uint256 public totalSupply;

    function decimals() external pure returns (uint8) { return 6; }
    function mint(address to, uint256 amount) external { balanceOf[to] += amount; totalSupply += amount; }
    function approve(address s, uint256 a) external returns (bool) { allowance[msg.sender][s] = a; return true; }
    function transfer(address to, uint256 a) external returns (bool) { _xfer(msg.sender, to, a); return true; }
    function transferFrom(address f, address to, uint256 a) external returns (bool) {
        uint256 allowed = allowance[f][msg.sender];
        if (allowed != type(uint256).max) require(allowed >= a, "allowance");
        if (allowed != type(uint256).max) allowance[f][msg.sender] = allowed - a;
        _xfer(f, to, a);
        return true;
    }
    function _xfer(address f, address t, uint256 a) private {
        require(balanceOf[f] >= a, "balance");
        balanceOf[f] -= a; balanceOf[t] += a;
    }
}

/// @dev Direct library harness so the LMSR-level guard stays covered even
///      though the market-level cap rejects huge shares first.
contract LMSRHarness {
    function buyCost(int256[] memory q, int256 b, uint256 idx, uint256 shares) external pure returns (uint256) {
        return LMSR.buyCost(q, b, idx, shares);
    }
}

contract SecurityFixesTest is Test {
    FakeUSDC usdc;
    OutcomeMarket market;
    LMSRHarness harness;

    address creator = address(0xA11CE);
    address trader1 = address(0xBEEF);
    address trader2 = address(0xC0DE);

    uint64 closesAt;

    function setUp() public {
        usdc = new FakeUSDC();
        market = new OutcomeMarket(address(usdc), creator);
        harness = new LMSRHarness();
        closesAt = uint64(block.timestamp + 1 days);

        usdc.mint(creator, 1_000_000e6);
        usdc.mint(trader1, 10_000e6);
        usdc.mint(trader2, 10_000e6);
    }

    function _create(uint8 outcomes, uint256 subsidy) internal returns (uint256 id) {
        vm.startPrank(creator);
        usdc.approve(address(market), type(uint256).max);
        id = market.createMarket(outcomes, subsidy, closesAt);
        vm.stopPrank();
    }

    function _buy(address who, uint256 id, uint8 outcome, uint256 shares)
        internal returns (uint256 cost)
    {
        uint256 beforeBal = usdc.balanceOf(who);
        vm.startPrank(who);
        usdc.approve(address(market), type(uint256).max);
        market.buy(id, outcome, shares, type(uint256).max);
        vm.stopPrank();
        cost = beforeBal - usdc.balanceOf(who);
    }

    // ------------------------------------------------------------------
    // Vulnerability #1: signed-cast wrap
    // ------------------------------------------------------------------

    function test_RevertOnHugeShares_MarketLevel() public {
        uint256 id = _create(2, 50e6);
        uint256 huge = uint256(type(int256).max) + 1;
        vm.prank(trader1);
        vm.expectRevert(OutcomeMarket.InvalidShares.selector);
        market.buy(id, 0, huge, type(uint256).max);
    }

    function test_RevertOnHugeShares_LMSRLevel() public {
        int256[] memory q = new int256[](2);
        uint256 huge = uint256(type(int256).max) + 1;
        vm.expectRevert(LMSR.SharesTooLarge.selector);
        harness.buyCost(q, 10_000_000, 0, huge);
    }

    function test_NormalSharesStillWork() public {
        uint256 id = _create(2, 50e6);
        uint256 cost = _buy(trader1, id, 0, 1_000_000);
        assertGt(cost, 0);
        assertEq(market.balanceOf(trader1, id * 256 + 0), 1_000_000);
    }

    function test_SupplyCapReverts() public {
        uint256 id = _create(2, 50e6);
        vm.startPrank(trader1);
        usdc.approve(address(market), type(uint256).max);
        vm.expectRevert(OutcomeMarket.InvalidShares.selector);
        market.buy(id, 0, market.MAX_SUPPLY_PER_OUTCOME() + 1, type(uint256).max);
        vm.stopPrank();
    }

    function test_ZeroSharesReverts() public {
        uint256 id = _create(2, 50e6);
        vm.prank(trader1);
        vm.expectRevert(OutcomeMarket.InvalidShares.selector);
        market.buy(id, 0, 0, type(uint256).max);
    }

    // ------------------------------------------------------------------
    // Vulnerability #2: cancelExpired refund recipient
    // ------------------------------------------------------------------

    /// The original PoC: trader buys, market expires, cancelExpired() runs.
    /// Pre-fix the TRADER's refund landed on the creator. Post-fix the
    /// creator receives nothing at cancel time and the trader pulls their
    /// share via claimCancelled().
    function test_CancelExpiredRefundsTraders_NotCreator() public {
        uint256 id = _create(2, 50e6);

        uint256 cost = _buy(trader1, id, 0, 5_000_000);
        uint256 escrow = 50e6 + cost;

        uint256 creatorBefore = usdc.balanceOf(creator);
        uint256 traderBefore = usdc.balanceOf(trader1);
        uint256 contractBefore = usdc.balanceOf(address(market));
        assertEq(contractBefore, escrow);

        vm.warp(closesAt + market.EXPIRY_WINDOW() + 1);
        market.cancelExpired(id);

        // Creator got NOTHING from the cancellation itself.
        assertEq(usdc.balanceOf(creator), creatorBefore, "creator must not be paid");

        // Trader claims their pro-rata slice.
        uint256 claimed = market.claimCancelled(id);
        assertGt(claimed, 0, "trader must receive a refund");
        assertLe(claimed, escrow);
        assertEq(usdc.balanceOf(trader1), traderBefore + claimed);
        assertEq(market.unclaimedEscrowMicro(id), escrow - claimed);
    }

    function test_CancelExpiredWithNoHoldersReturnsSubsidyToCreator() public {
        uint256 id = _create(2, 50e6);
        vm.warp(closesAt + market.EXPIRY_WINDOW() + 1);

        uint256 beforeBal = usdc.balanceOf(creator);
        market.cancelExpired(id);
        assertEq(usdc.balanceOf(creator) - beforeBal, 50e6);
        assertEq(market.unclaimedEscrowMicro(id), 0);
    }

    function test_TwoTradersConservation_LastClaimSweepsDustToCreator() public {
        uint256 id = _create(3, 50e6);

        _buy(trader1, id, 0, 4_000_000);
        _buy(trader2, id, 1, 6_000_000);

        uint256 escrow = usdc.balanceOf(address(market));
        assertGt(escrow, 50e6);

        vm.warp(closesAt + market.EXPIRY_WINDOW() + 1);

        uint256 creatorBefore = usdc.balanceOf(creator);
        uint256 t1Before = usdc.balanceOf(trader1);
        uint256 t2Before = usdc.balanceOf(trader2);

        market.cancelExpired(id);
        assertEq(usdc.balanceOf(creator), creatorBefore, "cancel pays nobody");

        uint256 c1 = market.claimCancelled(id);
        uint256 c2 = market.claimCancelled(id);

        // Conservation: every micro-USDC accounted for; contract drained;
        // floor-division dust swept to the creator on the final claim.
        assertEq(c1 + c2 <= escrow, true);
        assertEq(usdc.balanceOf(address(market)), 0, "contract drained");
        assertEq(usdc.balanceOf(trader1), t1Before + c1);
        assertEq(usdc.balanceOf(trader2), t2Before + c2);
        assertEq(usdc.balanceOf(creator), creatorBefore + (escrow - c1 - c2));
        assertEq(market.unclaimedEscrowMicro(id), 0);
    }

    function test_ClaimWithoutPositionReverts() public {
        uint256 id = _create(2, 50e6);
        _buy(trader1, id, 0, 1_000_000);

        vm.warp(closesAt + market.EXPIRY_WINDOW() + 1);
        market.cancelExpired(id);

        vm.prank(trader2); // holds nothing
        vm.expectRevert(OutcomeMarket.NothingToRedeem.selector);
        market.claimCancelled(id);
    }

    function test_TradingBlockedAfterCancel() public {
        uint256 id = _create(2, 50e6);
        vm.warp(closesAt + market.EXPIRY_WINDOW() + 1);
        market.cancelExpired(id);

        vm.startPrank(trader1);
        usdc.approve(address(market), type(uint256).max);
        vm.expectRevert(OutcomeMarket.AlreadyCancelled.selector);
        market.buy(id, 0, 1_000_000, type(uint256).max);
        vm.stopPrank();
    }
}
